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
db = SQLAlchemy(app)

# List of available items
ITEMS = ['Item 1', 'Item 2', 'Item 3', 'Item 4', 'Item 5', 'Curbs', 'Pipes']

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
    return render_template('project.html', 
                         project=project, 
                         items=ITEMS, 
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

# Create the database tables
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    # Initial price cache update
    update_price_cache()
    app.run(debug=os.getenv('FLASK_ENV') == 'development') 