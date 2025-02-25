from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash
from functools import wraps
from models import db, User
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded
from datetime import datetime, timedelta
import os
from validation import validate_login_data, validate_user_data, ValidationError, validate_string
import re

# Create Blueprint
auth = Blueprint('auth', __name__)

# Initialize LoginManager
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

# Initialize the rate limiter - will be attached to app in init_app()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per day", "30 per hour"],
    storage_uri="memory://",
    strategy="fixed-window",
    headers_enabled=True,
    retry_after="delta-seconds"
)

# Constants for login rate limiting
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = 15  # minutes

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('You need administrator privileges to access this page.', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

# Custom error handler for rate limiting
@auth.errorhandler(RateLimitExceeded)
def handle_rate_limit_exceeded(e):
    retry_after = 60  # Default value
    
    try:
        # Debug information
        print(f"Auth blueprint handler - Rate limit exceeded: {e}")
        print(f"Auth blueprint handler - Exception type: {type(e)}")
        
        # Try to get the actual retry_after value from the exception
        if hasattr(e, 'retry_after'):
            print(f"Found retry_after attribute: {e.retry_after}")
            retry_after = int(e.retry_after)
        elif hasattr(e, 'description'):
            print(f"Found description attribute: {e.description}")
            # Try to extract from the description (e.g., "5 per minute")
            description = str(e.description)
            if 'minute' in description:
                # Extract the number from the description
                try:
                    limit_value = int(''.join(filter(str.isdigit, description.split('per')[0])))
                    retry_after = 60  # 1 minute in seconds
                    print(f"Extracted from minute description: {retry_after}")
                except (ValueError, IndexError):
                    pass
            elif 'hour' in description:
                retry_after = 300  # 5 minutes in seconds
                print(f"Using hour-based retry_after: {retry_after}")
            elif 'day' in description:
                retry_after = 600  # 10 minutes in seconds
                print(f"Using day-based retry_after: {retry_after}")
    except Exception as ex:
        print(f"Error processing rate limit exception: {ex}")
        # Continue with default retry_after value
    
    try:
        print(f"Final retry_after value: {retry_after}")
        
        # Return an error page directly instead of redirecting to avoid loops
        return render_template('rate_limit_error.html', 
                               message='Too many login attempts. Please try again later.',
                               retry_after=retry_after), 429
    except Exception as template_ex:
        print(f"Error rendering template: {template_ex}")
        
        # If template rendering fails, return a simple HTML response
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Rate Limit Exceeded</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; margin-top: 50px; }}
                h1 {{ color: #e53e3e; }}
                p {{ margin: 20px 0; }}
                .countdown {{ font-weight: bold; }}
                .btn {{ display: inline-block; padding: 8px 16px; background-color: #3182ce; 
                       color: white; text-decoration: none; border-radius: 4px; }}
            </style>
        </head>
        <body>
            <h1>Rate Limit Exceeded</h1>
            <p>Too many login attempts. Please try again in <span class="countdown">{retry_after}</span> seconds.</p>
            <p><a href="{url_for('auth.login')}" class="btn">Return to Login</a></p>
            <script>
                let seconds = {retry_after};
                const countdownElement = document.querySelector('.countdown');
                const countdown = setInterval(function() {{
                    seconds--;
                    countdownElement.textContent = seconds;
                    if (seconds <= 0) {{
                        clearInterval(countdown);
                        window.location.href = "{url_for('auth.login')}";
                    }}
                }}, 1000);
            </script>
        </body>
        </html>
        """
        return html, 429

def init_app(app):
    """Initialize the authentication module with the Flask app"""
    try:
        # Initialize the login manager
        login_manager.init_app(app)
        
        print("Initializing rate limiter...")
        
        # Update the existing limiter with the app
        limiter.init_app(app)
        
        print(f"Rate limiter initialized: {limiter}")
        
        # Register the blueprint
        app.register_blueprint(auth, url_prefix='/auth')
        
        print(f"Auth blueprint registered with app")
        
        # Create admin user if none exists
        with app.app_context():
            try:
                # Use a raw SQL query to check if admin exists to avoid ORM issues during migration
                admin_exists = db.session.execute(db.text("SELECT COUNT(*) FROM user WHERE is_admin = 1")).scalar()
                
                if not admin_exists:
                    admin_username = 'admin'
                    admin_email = 'admin@example.com'
                    admin_password = 'admin'  # This should be changed immediately
                    
                    admin = User(username=admin_username, email=admin_email, is_admin=True)
                    admin.set_password(admin_password)
                    
                    db.session.add(admin)
                    db.session.commit()
                    
                    print("Admin user created with username 'admin' and password 'admin'. Please change this password immediately.")
            except Exception as e:
                print(f"Error creating admin user: {e}")
    except Exception as e:
        print(f"Error initializing auth module: {e}")
        # Continue without rate limiting rather than crashing
        pass

# Define the login route with rate limiting
@auth.route('/login', methods=['GET', 'POST'])
# Only apply rate limiting to POST requests, not GET
@limiter.limit("10 per minute", key_func=get_remote_address, 
               error_message="Too many login attempts. Please try again later.",
               methods=["POST"],
               exempt_when=lambda: current_user.is_authenticated)
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        try:
            # Validate login data
            validated_data = validate_login_data(request.form)
            username = validated_data['username']
            password = validated_data['password']
            remember = True if request.form.get('remember') else False
            
            user = User.query.filter_by(username=username).first()
            
            # Check if user exists
            if not user:
                flash('Please check your login details and try again.', 'error')
                return redirect(url_for('auth.login'))
            
            # Check if account is locked - only if the fields exist in the database
            if hasattr(user, 'locked_until') and user.locked_until and user.locked_until > datetime.utcnow():
                remaining_time = (user.locked_until - datetime.utcnow()).total_seconds() / 60
                flash(f'Account is locked due to too many failed attempts. Try again in {int(remaining_time)} minutes.', 'error')
                return redirect(url_for('auth.login'))
            
            # Check if the password is correct
            if not user.check_password(password):
                # Increment failed login attempts if the field exists
                if hasattr(user, 'failed_login_attempts'):
                    user.failed_login_attempts += 1
                    user.last_failed_login = datetime.utcnow()
                    
                    # Lock account after MAX_FAILED_ATTEMPTS
                    if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                        user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_DURATION)
                        flash(f'Account locked due to too many failed attempts. Try again in {LOCKOUT_DURATION} minutes.', 'error')
                    else:
                        remaining_attempts = MAX_FAILED_ATTEMPTS - user.failed_login_attempts
                        flash(f'Invalid password. {remaining_attempts} attempts remaining before account is locked.', 'error')
                    
                    db.session.commit()
                else:
                    flash('Please check your login details and try again.', 'error')
                
                return redirect(url_for('auth.login'))
            
            # Reset failed login attempts if login is successful
            if hasattr(user, 'failed_login_attempts'):
                user.failed_login_attempts = 0
                user.locked_until = None
                db.session.commit()
            
            # Log in the user
            login_user(user, remember=remember)
            return redirect(url_for('index'))
        except ValidationError as e:
            flash(str(e), 'error')
            return redirect(url_for('auth.login'))
        except Exception as e:
            current_app.logger.error(f"Error during login: {str(e)}")
            flash('An error occurred during login. Please try again.', 'error')
            return redirect(url_for('auth.login'))
    
    return render_template('login.html')

@auth.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))

@auth.route('/admin/users')
@login_required
@admin_required
def users():
    users = User.query.all()
    return render_template('users.html', users=users)

@auth.route('/admin/change_password', methods=['GET', 'POST'])
@login_required
@admin_required
def change_password():
    if request.method == 'POST':
        try:
            current_password = validate_string(request.form.get('current_password'), "Current password", min_length=8)
            new_password = validate_string(request.form.get('new_password'), "New password", min_length=8)
            confirm_password = validate_string(request.form.get('confirm_password'), "Confirm password", min_length=8)
            
            # Check if current password is correct
            if not current_user.check_password(current_password):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('auth.change_password'))
                
            # Check if new passwords match
            if new_password != confirm_password:
                flash('New passwords do not match.', 'error')
                return redirect(url_for('auth.change_password'))
                
            # Validate password strength
            if not (re.search(r'[A-Z]', new_password) and re.search(r'[a-z]', new_password) and 
                    re.search(r'\d', new_password) and re.search(r'[!@#$%^&*(),.?":{}|<>]', new_password)):
                flash('Password must contain at least one uppercase letter, one lowercase letter, one number, and one special character.', 'error')
                return redirect(url_for('auth.change_password'))
                
            # Update password
            current_user.set_password(new_password)
            db.session.commit()
            
            flash('Password updated successfully!', 'success')
            return redirect(url_for('auth.users'))
        except ValidationError as e:
            flash(str(e), 'error')
            return redirect(url_for('auth.change_password'))
        except Exception as e:
            current_app.logger.error(f"Error changing password: {str(e)}")
            flash('An error occurred while changing the password.', 'error')
            return redirect(url_for('auth.change_password'))
        
    return render_template('change_password.html')

@auth.route('/admin/create_user', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    if request.method == 'POST':
        try:
            # Validate user data
            validated_data = validate_user_data(request.form, is_new_user=True)
            
            # Check if username already exists
            if User.query.filter_by(username=validated_data['username']).first():
                flash('Username already exists.', 'error')
                return redirect(url_for('auth.create_user'))
                
            # Check if email already exists
            if User.query.filter_by(email=validated_data['email']).first():
                flash('Email already exists.', 'error')
                return redirect(url_for('auth.create_user'))
            
            # Create new user
            is_admin = request.form.get('is_admin') == 'on'
            new_user = User(
                username=validated_data['username'],
                email=validated_data['email'],
                is_admin=is_admin
            )
            new_user.set_password(validated_data['password'])
            
            db.session.add(new_user)
            db.session.commit()
            
            flash('User created successfully!', 'success')
            return redirect(url_for('auth.users'))
        except ValidationError as e:
            flash(str(e), 'error')
            return redirect(url_for('auth.create_user'))
        except Exception as e:
            current_app.logger.error(f"Error creating user: {str(e)}")
            flash('An error occurred while creating the user.', 'error')
            return redirect(url_for('auth.create_user'))
    
    return render_template('create_user.html')

@auth.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # Prevent deleting yourself
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('auth.users'))
        
    db.session.delete(user)
    db.session.commit()
    
    flash(f'User {user.username} deleted successfully.', 'success')
    return redirect(url_for('auth.users')) 