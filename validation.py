import re
import os
from werkzeug.utils import secure_filename
from flask import current_app
import validators
from urllib.parse import urlparse

# Try to import magic, but make it optional
try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False
    # Don't use current_app here as it's outside application context
    print("WARNING: python-magic or its dependencies (libmagic) not available. MIME type validation will be limited.")

class ValidationError(Exception):
    """Custom exception for validation errors"""
    pass

def validate_required(value, field_name):
    """Validate that a field is not empty"""
    if not value or (isinstance(value, str) and value.strip() == ''):
        raise ValidationError(f"{field_name} is required")
    return value

def validate_string(value, field_name, min_length=1, max_length=None, pattern=None, required=True):
    """Validate a string field"""
    if value is None or value == '':
        if required:
            raise ValidationError(f"{field_name} is required")
        return value
    
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    
    value = value.strip()
    
    if min_length and len(value) < min_length:
        raise ValidationError(f"{field_name} must be at least {min_length} characters")
    
    if max_length and len(value) > max_length:
        raise ValidationError(f"{field_name} must be at most {max_length} characters")
    
    if pattern and not re.match(pattern, value):
        raise ValidationError(f"{field_name} has an invalid format")
    
    # Sanitize the input to prevent XSS
    value = sanitize_string(value)
    
    return value

def sanitize_string(value):
    """Sanitize a string to prevent XSS attacks"""
    if not value:
        return value
    
    # Replace potentially dangerous characters
    value = value.replace('<', '&lt;').replace('>', '&gt;')
    return value

def validate_email(email, field_name="Email", required=True):
    """Validate an email address"""
    if not email:
        if required:
            raise ValidationError(f"{field_name} is required")
        return email
    
    email = email.strip().lower()
    
    # Basic email validation pattern
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        raise ValidationError(f"{field_name} is not a valid email address")
    
    return email

def validate_phone(phone, field_name="Phone", required=False):
    """Validate a phone number"""
    if not phone:
        if required:
            raise ValidationError(f"{field_name} is required")
        return phone
    
    # Remove common formatting characters
    cleaned_phone = re.sub(r'[\s\-\(\)\.]', '', phone)
    
    # Check if it's a valid phone number (basic check)
    if not re.match(r'^(\+\d{1,3})?(\d{10,15})$', cleaned_phone):
        raise ValidationError(f"{field_name} is not a valid phone number")
    
    return phone  # Return original format to preserve user input formatting

def validate_date(date, field_name="Date", required=False):
    """Validate a date string (accepts both MM/DD/YYYY and YYYY-MM-DD formats)"""
    if not date:
        if required:
            raise ValidationError(f"{field_name} is required")
        return date
    
    date = date.strip()
    
    # Check for HTML5 date input format (YYYY-MM-DD)
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        # Already in the correct format for database storage
        return date
    
    # Check for traditional date format (MM/DD/YYYY)
    if re.match(r'^(0[1-9]|1[0-2])/(0[1-9]|[12][0-9]|3[01])/\d{4}$', date):
        # Convert to YYYY-MM-DD format for database storage
        month, day, year = date.split('/')
        return f"{year}-{month}-{day}"
    
    raise ValidationError(f"{field_name} must be in format MM/DD/YYYY or YYYY-MM-DD")

def validate_number(value, field_name, min_value=None, max_value=None, required=True):
    """Validate a numeric value"""
    if value is None or value == '':
        if required:
            raise ValidationError(f"{field_name} is required")
        return None
    
    try:
        if isinstance(value, str):
            # Remove any commas or currency symbols
            value = value.replace(',', '').replace('$', '').strip()
        
        num_value = float(value)
        
        if min_value is not None and num_value < min_value:
            raise ValidationError(f"{field_name} must be at least {min_value}")
        
        if max_value is not None and num_value > max_value:
            raise ValidationError(f"{field_name} must be at most {max_value}")
        
        return num_value
    except ValueError:
        raise ValidationError(f"{field_name} must be a valid number")

def validate_integer(value, field_name, min_value=None, max_value=None, required=True):
    """Validate an integer value"""
    num_value = validate_number(value, field_name, min_value, max_value, required)
    
    if num_value is None:
        return None
    
    if not float(num_value).is_integer():
        raise ValidationError(f"{field_name} must be a whole number")
    
    return int(num_value)

def validate_file_upload(file, field_name="File", required=False, allowed_extensions=None, max_size_mb=16):
    """Validate a file upload"""
    if not file or file.filename == '':
        if required:
            raise ValidationError(f"{field_name} is required")
        return None
    
    filename = secure_filename(file.filename)
    
    # Check file extension
    if allowed_extensions:
        ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        if ext not in allowed_extensions:
            raise ValidationError(f"{field_name} must be one of the following types: {', '.join(allowed_extensions)}")
    
    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)  # Reset file pointer
    
    max_size_bytes = max_size_mb * 1024 * 1024
    if file_size > max_size_bytes:
        raise ValidationError(f"{field_name} exceeds maximum size of {max_size_mb}MB")
    
    # Check file content type (requires python-magic)
    if MAGIC_AVAILABLE and allowed_extensions and any(ext in ['jpg', 'jpeg', 'png', 'gif'] for ext in allowed_extensions):
        try:
            file_content = file.read(2048)  # Read first 2048 bytes for mime detection
            file.seek(0)  # Reset file pointer
            
            mime = magic.Magic(mime=True)
            mime_type = mime.from_buffer(file_content)
            
            # Validate image files
            if not mime_type.startswith('image/'):
                raise ValidationError(f"{field_name} is not a valid image file")
        except Exception as e:
            # Log the error but continue with basic validation
            try:
                if hasattr(current_app, 'logger') and current_app.logger:
                    current_app.logger.error(f"Error validating file content: {str(e)}")
                else:
                    print(f"Error validating file content: {str(e)}")
            except RuntimeError:
                # Handle case when outside application context
                print(f"Error validating file content: {str(e)}")
    else:
        # Fallback validation for image files when magic is not available
        if allowed_extensions and any(ext in ['jpg', 'jpeg', 'png', 'gif'] for ext in allowed_extensions):
            # We can only check the extension, which we already did above
            try:
                if hasattr(current_app, 'logger') and current_app.logger:
                    current_app.logger.warning(f"MIME type validation skipped for {filename} - python-magic not available")
                else:
                    print(f"MIME type validation skipped for {filename} - python-magic not available")
            except RuntimeError:
                # Handle case when outside application context
                print(f"MIME type validation skipped for {filename} - python-magic not available")
    
    return file

def validate_address(address, field_name="Address", required=False, min_length=5, max_length=200):
    """Validate an address string"""
    if not address:
        if required:
            raise ValidationError(f"{field_name} is required")
        return address
    
    address = address.strip()
    address = sanitize_string(address)
    
    if len(address) < min_length:
        raise ValidationError(f"{field_name} must be at least {min_length} characters")
    
    if len(address) > max_length:
        raise ValidationError(f"{field_name} must be at most {max_length} characters")
    
    # Remove the strict validation that requires numbers and letters
    # This allows for more flexible address formats
    
    return address

def validate_url(url, field_name="URL", required=False):
    """Validate a URL"""
    if not url:
        if required:
            raise ValidationError(f"{field_name} is required")
        return url
    
    url = url.strip()
    
    # Use the validators library for URL validation
    if not validators.url(url):
        raise ValidationError(f"{field_name} is not a valid URL")
    
    # Additional security checks
    parsed_url = urlparse(url)
    if parsed_url.scheme not in ['http', 'https']:
        raise ValidationError(f"{field_name} must use http or https protocol")
    
    return url

def validate_project_data(data):
    """Validate all project data fields"""
    validated = {}
    
    validated['name'] = validate_string(data.get('project_name'), "Project name", max_length=100)
    validated['date'] = validate_date(data.get('date'), "Date")
    validated['attn'] = validate_string(data.get('attn'), "Attention", required=False, max_length=100)
    validated['contractor_name'] = validate_string(data.get('contractor_name'), "Contractor name", required=False, max_length=100)
    validated['contractor_email'] = validate_email(data.get('contractor_email'), "Contractor email", required=False)
    validated['job_contact'] = validate_string(data.get('job_contact'), "Job contact", required=False, max_length=100)
    validated['job_contact_phone'] = validate_phone(data.get('job_contact_phone'), "Job contact phone", required=False)
    validated['address'] = validate_address(data.get('address'), "Address", required=False)
    
    return validated

def validate_item_data(data):
    """Validate item data"""
    validated = {}
    
    validated['name'] = validate_string(data.get('item') or data.get('name'), "Item name", max_length=100)
    validated['quantity'] = validate_integer(data.get('quantity'), "Quantity", min_value=1)
    validated['price'] = validate_number(data.get('price'), "Price", min_value=0)
    
    return validated

def validate_login_data(data):
    """Validate login data"""
    validated = {}
    
    validated['username'] = validate_string(data.get('username'), "Username", min_length=3, max_length=80)
    validated['password'] = validate_string(data.get('password'), "Password", min_length=8)
    
    return validated

def validate_user_data(data, is_new_user=True):
    """Validate user data"""
    validated = {}
    
    validated['username'] = validate_string(data.get('username'), "Username", min_length=3, max_length=80)
    validated['email'] = validate_email(data.get('email'), "Email")
    
    if is_new_user or data.get('password'):
        password = validate_string(data.get('password'), "Password", min_length=8)
        # Check password strength
        if not (re.search(r'[A-Z]', password) and re.search(r'[a-z]', password) and 
                re.search(r'\d', password) and re.search(r'[!@#$%^&*(),.?":{}|<>]', password)):
            raise ValidationError("Password must contain at least one uppercase letter, one lowercase letter, one number, and one special character")
        validated['password'] = password
    
    return validated 