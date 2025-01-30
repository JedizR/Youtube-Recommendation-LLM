# app.py
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from mistralai import Mistral
import dotenv
from googleapiclient.discovery import build
import random
import json
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from functools import wraps
import re

# Load environment variables
dotenv.load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')  # Change this in production

# Initialize Mistral client
mistral_client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
model = "mistral-large-latest"

# Initialize YouTube API client
youtube = build('youtube', 'v3', developerKey=os.environ["YOUTUBE_API_KEY"])

# Database initialization
def init_db():
    with sqlite3.connect('database.db') as conn:
        c = conn.cursor()
        # Create users table
        c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Create favorites table
        c.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            video_id TEXT NOT NULL,
            video_title TEXT NOT NULL,
            keyword TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            UNIQUE(user_id, video_id)
        )
        ''')
        conn.commit()

init_db()

# Login decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Sample prompts for random generation
SAMPLE_PROMPTS = [
    "I want to learn about space exploration",
    "Show me some cooking tutorials",
    "I'm interested in historical documentaries",
    "I want to watch gaming content",
    "Show me DIY home improvement videos",
    "I want to learn about artificial intelligence",
]

SYSTEM_PROMPT = """Given a user's interest in watching videos, generate 4 different search keywords that would help find relevant YouTube videos. Return ONLY a JSON array with exactly 4 strings, no additional text. Make the keywords specific and varied to cover different aspects of the topic.

Example input: "I want to learn about machine learning"
Example output: ["introduction to machine learning tutorial", "machine learning projects for beginners", "how neural networks work explained", "practical machine learning applications"]"""

def get_search_keywords(user_input):
    """Get search keywords from Mistral AI"""
    response = mistral_client.chat.complete(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_input}
        ]
    )
    try:
        keywords = json.loads(response.choices[0].message.content)
        return keywords if len(keywords) == 4 else ["error parsing keywords"] * 4
    except:
        return ["error parsing keywords"] * 4

def search_youtube_videos(keyword):
    """Search YouTube for videos matching the keyword"""
    try:
        request = youtube.search().list(
            part="snippet",
            q=keyword,
            type="video",
            maxResults=1,
            regionCode="US",
            relevanceLanguage="en",
            safeSearch="moderate"
        )
        response = request.execute()
        
        if response.get("items"):
            video_id = response["items"][0]["id"]["videoId"]
            title = response["items"][0]["snippet"]["title"]
            thumbnail = response["items"][0]["snippet"]["thumbnails"]["default"]["url"]
            return {
                "video_id": video_id,
                "title": title,
                "thumbnail": thumbnail,
                "keyword": keyword
            }
        return None
    except Exception as e:
        print(f"Error searching YouTube: {e}")
        return None

# Auth routes
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        
        # Validation
        if not re.match("^[a-zA-Z0-9_]{3,20}$", username):
            flash('Username must be 3-20 characters long and contain only letters, numbers, and underscores')
            return redirect(url_for('register'))
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long')
            return redirect(url_for('register'))
            
        try:
            with sqlite3.connect('database.db') as conn:
                c = conn.cursor()
                hashed_password = generate_password_hash(password)
                c.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                         (username, hashed_password))
                conn.commit()
                flash('Registration successful! Please login.')
                return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists')
            return redirect(url_for('register'))
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        with sqlite3.connect('database.db') as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE username = ?', (username,))
            user = c.fetchone()
            
            if user and check_password_hash(user[2], password):
                session['user_id'] = user[0]
                session['username'] = user[1]
                flash('Logged in successfully!')
                return redirect(url_for('search'))
            else:
                flash('Invalid username or password')
                
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully!')
    return redirect(url_for('login'))

# Main routes
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('search'))
    return redirect(url_for('login'))

@app.route('/search')
@login_required
def search():
    return render_template('search.html', username=session.get('username'))

@app.route('/favorites')
@login_required
def favorites():
    with sqlite3.connect('database.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT video_id, video_title, keyword
            FROM favorites
            WHERE user_id = ?
            ORDER BY added_at DESC
        ''', (session['user_id'],))
        favorites = [
            {
                'video_id': row[0],
                'title': row[1],
                'keyword': row[2]
            }
            for row in c.fetchall()
        ]
    return render_template('favorites.html', favorites=favorites, username=session.get('username'))

# API routes
@app.route('/get_recommendations', methods=['POST'])
@login_required
def get_recommendations():
    user_input = request.form.get('user_input')
    keywords = get_search_keywords(user_input)
    videos = []
    
    for keyword in keywords:
        video = search_youtube_videos(keyword)
        if video:
            # Check if video is in favorites
            with sqlite3.connect('database.db') as conn:
                c = conn.cursor()
                c.execute('SELECT 1 FROM favorites WHERE user_id = ? AND video_id = ?',
                         (session['user_id'], video['video_id']))
                video['is_favorite'] = bool(c.fetchone())
            videos.append(video)
        else:
            videos.append({"video_id": None, "title": "Video not found", "keyword": keyword})
    
    return jsonify({
        "keywords": keywords,
        "videos": videos
    })

@app.route('/random_prompt')
@login_required
def random_prompt():
    return jsonify({"prompt": random.choice(SAMPLE_PROMPTS)})

@app.route('/toggle_favorite', methods=['POST'])
@login_required
def toggle_favorite():
    video_id = request.form.get('video_id')
    video_title = request.form.get('video_title')
    keyword = request.form.get('keyword')
    
    with sqlite3.connect('database.db') as conn:
        c = conn.cursor()
        # Check if already favorited
        c.execute('SELECT 1 FROM favorites WHERE user_id = ? AND video_id = ?',
                 (session['user_id'], video_id))
        exists = c.fetchone()
        
        if exists:
            c.execute('DELETE FROM favorites WHERE user_id = ? AND video_id = ?',
                     (session['user_id'], video_id))
            is_favorite = False
        else:
            c.execute('''
                INSERT INTO favorites (user_id, video_id, video_title, keyword)
                VALUES (?, ?, ?, ?)
            ''', (session['user_id'], video_id, video_title, keyword))
            is_favorite = True
        
        conn.commit()
        
    return jsonify({"success": True, "is_favorite": is_favorite})

if __name__ == '__main__':
    app.run(debug=True)