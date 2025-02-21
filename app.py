from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sheets_helper import get_sheet_data
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
import threading
import time
import io
from docx.shared import Inches
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from dotenv import load_dotenv
import os
from werkzeug.utils import secure_filename

# Load environment variables from .env file
load_dotenv()

# Now you can access environment variables using os.getenv()
# Example:
# database_url = os.getenv('DATABASE_URL')
# google_client_id = os.getenv('GOOGLE_CLIENT_ID')

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///items.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'default-dev-key-please-change')
app.config['UPLOAD_FOLDER'] = 'static/item_images'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# List of available items with their images
class ItemDefinition:
    def __init__(self, name, image_path=None):
        self.name = name
        self.image_path = image_path

ITEMS = []

def save_items_to_file():
    """Save the ITEMS list to a file"""
    with open('items.txt', 'w') as f:
        for item in ITEMS:
            f.write(f"{item.name}|{item.image_path or ''}\n")

def load_items_from_file():
    """Load items from file if it exists"""
    global ITEMS
    try:
        with open('items.txt', 'r') as f:
            ITEMS = []
            for line in f:
                if line.strip():
                    parts = line.strip().split('|')
                    name = parts[0]
                    image_path = parts[1] if len(parts) > 1 and parts[1] else None
                    ITEMS.append(ItemDefinition(name, image_path))
    except FileNotFoundError:
        # If file doesn't exist, save current items
        ITEMS = [ItemDefinition(name) for name in ['Curbs', 'Pipes']]
        save_items_to_file()

# Load items when app starts
load_items_from_file()

# Cache for prices
price_cache = {}
last_update = 0
CACHE_DURATION = 300  # 5 minutes in seconds

def update_price_cache():
    global price_cache, last_update
    current_time = time.time()
    
    # Update cache if it's expired
    if current_time - last_update > CACHE_DURATION:
        new_prices = get_sheet_data()
        if new_prices:  # Only update if we got data
            price_cache = new_prices
            last_update = current_time

# Database Models
class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('Item', backref='project', lazy=True)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def translate_to_words(items):
    if not items:
        return "No items selected."
    
    item_counts = {}
    for item in items:
        if item.name in item_counts:
            item_counts[item.name] += item.quantity
        else:
            item_counts[item.name] = item.quantity
    
    parts = []
    
    for item, count in item_counts.items():
        if item == "Curbs":
            curb_text = "Curb" if count == 1 else "Curbs"
            parts.append(f"- Tie-In / Flash ({count}) {curb_text} with roofing material compatible to existing material.")
        elif item == "Pipes":
            pipe_text = "pipe / penetration" if count == 1 else "pipes / penetrations"
            parts.append(f"- Tie-In / Flash ({count}) {pipe_text} with roofing material compatible to existing material.")
        elif item == "Item 1":
            parts.append(f"- {count} flashing {item}.")
        elif item == "Item 2":
            parts.append(f"- {count} {item} panels.")
        else:
            parts.append(f"- {count} {item}.")
    
    # Join with double line breaks
    if len(parts) > 1:
        return "\n\n".join(parts)
    else:
        return parts[0]

@app.route('/')
def index():
    projects = Project.query.order_by(Project.created_at.desc()).all()
    return render_template('index.html', projects=projects)

@app.route('/project/<int:project_id>')
def project(project_id):
    update_price_cache()  # Update prices when viewing a project
    project = Project.query.get_or_404(project_id)
    translation = translate_to_words(project.items)
    
    # Pass the items list with their names and image paths
    items_with_images = [{'name': item.name, 'image_path': item.image_path} for item in ITEMS]
    
    return render_template('project.html', 
                         project=project, 
                         items=items_with_images, 
                         translation=translation,
                         price_cache=price_cache)

@app.route('/get_price/<item_name>')
def get_price(item_name):
    update_price_cache()
    price = price_cache.get(item_name, None)
    return jsonify({'price': price})

@app.route('/create_project', methods=['POST'])
def create_project():
    name = request.form.get('project_name')
    if name:
        project = Project(name=name)
        db.session.add(project)
        db.session.commit()
        return redirect(url_for('project', project_id=project.id))
    return redirect(url_for('index'))

@app.route('/add_item/<int:project_id>', methods=['POST'])
def add_item(project_id):
    project = Project.query.get_or_404(project_id)
    data = request.json
    
    item = Item(
        name=data['item'],
        quantity=data['quantity'],
        price=data['price'],
        project=project
    )
    
    db.session.add(item)
    db.session.commit()
    
    translation = translate_to_words(project.items)
    return jsonify({
        'success': True, 
        'items': [{
            'name': item.name,
            'quantity': item.quantity,
            'price': item.price,
            'total': item.quantity * item.price
        } for item in project.items],
        'translation': translation
    })

@app.route('/clear_items/<int:project_id>', methods=['POST'])
def clear_items(project_id):
    project = Project.query.get_or_404(project_id)
    for item in project.items:
        db.session.delete(item)
    db.session.commit()
    
    translation = translate_to_words([])
    return jsonify({'success': True, 'items': [], 'translation': translation})

@app.route('/delete_project/<int:project_id>', methods=['POST'])
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)
    db.session.delete(project)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/generate_word/<int:project_id>', methods=['POST'])
def generate_word(project_id):
    try:
        project = Project.query.get_or_404(project_id)
        
        # Create a new Word document
        doc = Document()
        
        # Add the name paragraph
        para = doc.add_paragraph()
        name_label = para.add_run("Name: ")
        name_label.font.name = "Tahoma"
        name_label.font.size = Pt(10)
        
        # Create XML elements for bookmark
        def add_bookmark(paragraph, bookmark_text, bookmark_name):
            run = paragraph.add_run()
            tag = OxmlElement('w:bookmarkStart')
            tag.set(qn('w:id'), '0')
            tag.set(qn('w:name'), bookmark_name)
            run._r.append(tag)
            
            run = paragraph.add_run(bookmark_text)
            run.font.name = "Tahoma"
            run.font.size = Pt(10)
            
            tag = OxmlElement('w:bookmarkEnd')
            tag.set(qn('w:id'), '0')
            run._r.append(tag)
            return run
        
        # Add the project name with bookmark
        add_bookmark(para, project.name, "NameField")
        
        # Save the document to a BytesIO object
        doc_buffer = io.BytesIO()
        doc.save(doc_buffer)
        doc_buffer.seek(0)
        
        return send_file(
            doc_buffer,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=f'{project.name}_proposal.docx'
        )
    except Exception as e:
        print(f"Error generating Word document: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/admin')
def admin():
    return render_template('admin.html', items=ITEMS)

@app.route('/admin/items/add', methods=['POST'])
def admin_add_item():
    global ITEMS
    name = request.form.get('name')
    image = request.files.get('image')
    image_path = None
    
    if name and name not in [item.name for item in ITEMS]:
        if image and allowed_file(image.filename):
            filename = secure_filename(image.filename)
            # Add timestamp to filename to prevent duplicates
            filename = f"{int(time.time())}_{filename}"
            image_path = os.path.join('/static/item_images', filename)  # Add /static/ prefix
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        ITEMS.append(ItemDefinition(name, image_path))
        ITEMS.sort(key=lambda x: x.name)  # Keep items sorted by name
        save_items_to_file()
        return jsonify({'success': True})
    
    return jsonify({'success': False}), 400

@app.route('/admin/items/edit', methods=['POST'])
def admin_edit_item():
    global ITEMS
    old_name = request.form.get('oldName')
    new_name = request.form.get('newName')
    image = request.files.get('image')
    keep_image = request.form.get('keepImage') == 'true'
    
    old_item = next((item for item in ITEMS if item.name == old_name), None)
    if old_item and new_name:
        # Only check for name conflict if the name is actually changing
        if new_name != old_name and new_name in [item.name for item in ITEMS if item.name != old_name]:
            return jsonify({'success': False, 'error': 'Name already exists'}), 400

        # Handle image update
        image_path = old_item.image_path if keep_image else None
        if image and allowed_file(image.filename):
            # Delete old image if exists
            if old_item.image_path:
                old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(old_item.image_path))
                if os.path.exists(old_image_path):
                    os.remove(old_image_path)
            
            filename = secure_filename(image.filename)
            filename = f"{int(time.time())}_{filename}"
            image_path = os.path.join('/static/item_images', filename)  # Add /static/ prefix
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        elif not keep_image and old_item.image_path:
            # Delete old image if exists and not keeping it
            old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(old_item.image_path))
            if os.path.exists(old_image_path):
                os.remove(old_image_path)
        
        # Update item in ITEMS list
        old_item.name = new_name
        old_item.image_path = image_path
        ITEMS.sort(key=lambda x: x.name)  # Keep items sorted
        save_items_to_file()
        
        # Update any existing items in projects
        if new_name != old_name:
            items_to_update = Item.query.filter_by(name=old_name).all()
            for item in items_to_update:
                item.name = new_name
            db.session.commit()
        
        return jsonify({'success': True})
    
    return jsonify({'success': False}), 400

@app.route('/admin/items/delete', methods=['POST'])
def admin_delete_item():
    global ITEMS
    name = request.json.get('name')
    
    item_to_delete = next((item for item in ITEMS if item.name == name), None)
    if item_to_delete:
        # Delete image if exists
        if item_to_delete.image_path:
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(item_to_delete.image_path))
            if os.path.exists(image_path):
                os.remove(image_path)
        
        ITEMS.remove(item_to_delete)
        save_items_to_file()
        
        # Delete any existing items in projects
        items_to_delete = Item.query.filter_by(name=name).all()
        for item in items_to_delete:
            db.session.delete(item)
        db.session.commit()
        
        return jsonify({'success': True})
    
    return jsonify({'success': False}), 400

# Create the database tables
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    # Initial price cache update
    update_price_cache()
    app.run(debug=os.getenv('FLASK_ENV') == 'development') 