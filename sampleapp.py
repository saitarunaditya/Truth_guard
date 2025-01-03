from flask import Flask, request, jsonify, Response, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import os
import numpy as np
import joblib
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import feedparser
import requests
import json
import subprocess
import threading
import time
from difflib import SequenceMatcher
import tempfile
from werkzeug.serving import run_simple
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import assemblyai as aai
from dotenv import load_dotenv
import queue
import concurrent.futures
from pathlib import Path

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Constants
PORT = int(os.getenv('PORT', 3000))
NEWS_UPDATE_INTERVAL = 300  # 5 minutes
CACHE_CLEANUP_INTERVAL = 3600  # 1 hour

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=10000, backupCount=3)
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
handler.setFormatter(formatter)
logger.addHandler(handler)

# News Sources Configuration
NEWS_SOURCES = {
    'RSS_FEEDS': [
        {
            'url': 'https://timesofindia.indiatimes.com/rssfeedstopstories.cms',
            'name': 'Times of India',
            'reliability': 0.8
        },
        {
            'url': 'https://www.thehindu.com/news/national/feeder/default.rss',
            'name': 'The Hindu',
            'reliability': 0.85
        }
    ],
    'GNEWS': {
        'endpoint': 'https://gnews.io/api/v4/top-headlines',
        'params': {
            'country': 'in',
            'lang': 'en',
            'max': 10,
            'token': os.getenv('GNEWS_API_KEY')
        }
    }
}

# Initialize AssemblyAI
aai.settings.api_key = os.getenv('ASSEMBLYAI_API_KEY')

# ML Model class definition
class EnsembleModel:
    def __init__(self, models):
        self.models = models

    def predict(self, X):
        predictions = np.zeros((X.shape[0], 2))
        for model in self.models.values():
            predictions += model.predict_proba(X)
        proba = predictions / len(self.models)
        return (proba[:, 1] > 0.5).astype(int), proba[:, 1]

# Cache Manager
class CacheManager:
    def __init__(self):
        self.caches = {
            'audio': {},
            'news': {},
            'analysis': {},
            'live_streams': {},
            'analyzers': {}
        }
        self._lock = threading.Lock()
    
    def get(self, cache_type, key):
        with self._lock:
            cache = self.caches[cache_type]
            item = cache.get(key)
            if item and not self._is_expired(item['timestamp'], item['ttl']):
                return item['data']
            return None

    def set(self, cache_type, key, data, ttl=3600):
        with self._lock:
            self.caches[cache_type][key] = {
                'data': data,
                'timestamp': time.time(),
                'ttl': ttl
            }

    def delete(self, cache_type, key):
        with self._lock:
            if key in self.caches[cache_type]:
                del self.caches[cache_type][key]

    def clear(self, cache_type=None):
        with self._lock:
            if cache_type:
                self.caches[cache_type].clear()
            else:
                for cache in self.caches.values():
                    cache.clear()

    def cleanup(self):
        with self._lock:
            current_time = time.time()
            for cache_type in self.caches:
                expired_keys = [
                    key for key, item in self.caches[cache_type].items()
                    if self._is_expired(item['timestamp'], item['ttl'])
                ]
                for key in expired_keys:
                    del self.caches[cache_type][key]

    def _is_expired(self, timestamp, ttl):
        return (time.time() - timestamp) > ttl

    def get_stats(self):
        with self._lock:
            return {
                cache_type: len(cache)
                for cache_type, cache in self.caches.items()
            }

cache_manager = CacheManager()

# Buffer Management
class OptimizedSlidingBuffer:
    def __init__(self, max_duration=60000, chunk_size=10000):
        self.max_duration = max_duration
        self.chunk_size = chunk_size
        self.chunks = []
        self.total_duration = 0
        self.last_processed = 0
        self._lock = threading.Lock()

    def add_chunk(self, chunk, duration):
        with self._lock:
            now = time.time() * 1000
            self.chunks.append({
                'chunk': chunk,
                'duration': duration,
                'timestamp': now
            })
            self.total_duration += duration

            while self.total_duration > self.max_duration:
                removed = self.chunks.pop(0)
                self.total_duration -= removed['duration']

            return self._should_process(now)

    def _should_process(self, now):
        return (now - self.last_processed) >= self.chunk_size

    def get_buffer(self):
        with self._lock:
            self.last_processed = time.time() * 1000
            return b''.join([c['chunk'] for c in self.chunks])

    def clear(self):
        with self._lock:
            self.chunks = []
            self.total_duration = 0

# Stream Processor
class StreamProcessor:
    def __init__(self, socket_id):
        self.socket_id = socket_id
        self.buffer = OptimizedSlidingBuffer()
        self.processing_queue = queue.Queue()
        self.is_processing = False
        self.should_stop = False
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.start_processing_thread()

    def start_processing_thread(self):
        self.thread_pool.submit(self._process_queue)

    def process_chunk(self, chunk, duration):
        if not self.buffer.add_chunk(chunk, duration):
            return

        self.processing_queue.put({
            'buffer': self.buffer.get_buffer(),
            'timestamp': time.time()
        })

    def _process_queue(self):
        while not self.should_stop:
            try:
                if self.is_processing:
                    continue

                item = self.processing_queue.get(timeout=1)
                self.is_processing = True

                self._process_audio(item['buffer'], item['timestamp'])

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f'Stream processing error: {str(e)}')
                socketio.emit('error', {
                    'error': 'Stream processing failed',
                    'details': str(e)
                }, room=self.socket_id)
            finally:
                self.is_processing = False

    def _process_audio(self, buffer, timestamp):
        try:
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
                temp_file.write(buffer)
                temp_file_path = temp_file.name

            audio_file = aai.transcripts.upload_audio_file(temp_file_path)
            transcript = aai.transcripts.create(
                audio_file,
                language_code='en'
            )

            processed_text = pipeline.transform([transcript.text])
            prediction, confidence = ensemble_model.predict(processed_text)

            socketio.emit('transcription', {
                'text': transcript.text,
                'analysis': {
                    'prediction': bool(prediction[0]),
                    'confidence': float(confidence[0])
                },
                'timestamp': timestamp
            }, room=self.socket_id)

        except Exception as e:
            raise e
        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def stop(self):
        self.should_stop = True
        self.thread_pool.shutdown(wait=False)
        self.buffer.clear()

# Load ML models with proper error handling
try:
    MODEL_PATH = 'saved_models/ensemble_model.pkl'
    PIPELINE_PATH = 'saved_models/pipeline.pkl'
    
    if not os.path.exists(MODEL_PATH) or not os.path.exists(PIPELINE_PATH):
        logger.error("Model files not found. Creating dummy models for testing.")
        class DummyModel:
            def predict_proba(self, X):
                return np.array([[0.3, 0.7]] * len(X))
                
        class DummyPipeline:
            def transform(self, X):
                return X
                
        ensemble_model = {'model1': DummyModel()}
        pipeline = DummyPipeline()
    else:
        ensemble_model = joblib.load(MODEL_PATH)
        pipeline = joblib.load(PIPELINE_PATH)
except Exception as e:
    logger.error(f"Error loading models: {str(e)}")
    class DummyModel:
        def predict_proba(self, X):
            return np.array([[0.3, 0.7]] * len(X))
            
    class DummyPipeline:
        def transform(self, X):
            return X
            
    ensemble_model = {'model1': DummyModel()}
    pipeline = DummyPipeline()

# Utility Functions
def detect_platform(url):
    try:
        domain = urlparse(url).netloc.lower()
        platform_map = {
            'youtube.com': 'youtube',
            'youtu.be': 'youtube',
            'instagram.com': 'instagram',
            'facebook.com': 'facebook',
            'fb.com': 'facebook',
            'tiktok.com': 'tiktok',
            'twitter.com': 'twitter',
            'vimeo.com': 'vimeo'
        }
        
        for key, value in platform_map.items():
            if key in domain:
                return value
        return 'unknown'
    except:
        raise ValueError('Invalid URL format')

def fetch_gnews_articles():
    try:
        api_key = os.getenv('GNEWS_API_KEY')
        if not api_key:
            logger.warning('GNews API key is not configured')
            return []

        response = requests.get(
            NEWS_SOURCES['GNEWS']['endpoint'],
            params={**NEWS_SOURCES['GNEWS']['params'], 'token': api_key},
            timeout=10
        )
        
        if response.status_code == 200:
            return response.json().get('articles', [])
        elif response.status_code == 401:
            logger.error('Invalid GNews API key')
            return []
        else:
            logger.error(f'GNews API returned status code: {response.status_code}')
            return []
    except requests.exceptions.RequestException as e:
        logger.error(f'GNews API Request Error: {str(e)}')
        return []
    except Exception as e:
        logger.error(f'GNews API Error: {str(e)}')
        return []

def scrape_article(url):
    try:
        response = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }, timeout=10)
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for elem in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            elem.decompose()
        
        article = soup.find('article') or soup.find('main') or soup.find('body')
        
        paragraphs = []
        for p in article.find_all('p'):
            text = p.get_text().strip()
            if len(text) > 30:
                paragraphs.append(text)
        
        return ' '.join(paragraphs)
    except Exception as e:
        logger.error(f'Error scraping article: {str(e)}')
        return None

def fetch_trending_news():
    cached_news = cache_manager.get('news', 'trending')
    if cached_news:
        return cached_news

    try:
        all_news = []

        for source in NEWS_SOURCES['RSS_FEEDS']:
            try:
                feed = feedparser.parse(source['url'])

                for item in feed.entries[:10]:
                    try:
                        if hasattr(item, 'published_parsed') and item.published_parsed:
                            pub_date = datetime(*item.published_parsed[:6])
                        elif hasattr(item, 'published'):
                            for date_format in [
                                '%a, %d %b %Y %H:%M:%S %z',
                                '%Y-%m-%dT%H:%M:%S%z',
                                '%Y-%m-%d %H:%M:%S',
                            ]:
                                try:
                                    pub_date = datetime.strptime(item.published, date_format)
                                    break
                                except ValueError:
                                    continue
                            else:
                                pub_date = datetime.now()
                        else:
                            pub_date = datetime.now()
                    except Exception as e:
                        logger.warning(f"Date parsing error: {str(e)}")
                        pub_date = datetime.now()

                    news_item = {
                        'title': getattr(item, 'title', '').strip(),
                        'description': getattr(item, 'description', '').strip(),
                        'url': getattr(item, 'link', ''),
                        'source': source['name'],
                        'reliability': source['reliability'],
                        'published': pub_date.isoformat(),
                        'type': 'rss'
                    }

                    if news_item['title']:
                        all_news.append(news_item)

            except Exception as e:
                logger.error(f"RSS feed error for {source['name']}: {str(e)}")
                continue

        gnews_articles = fetch_gnews_articles()
        for article in gnews_articles:
            try:
                pub_date = datetime.strptime(
                    article.get('publishedAt', datetime.now().isoformat()),
                    '%Y-%m-%dT%H:%M:%SZ'
                )
            except ValueError:
                pub_date = datetime.now()

            news_item = {
                'title': article.get('title', '').strip(),
                'description': article.get('description', '').strip(),
                'url': article.get('url', ''),
                'source': article.get('source', {}).get('name', 'GNews'),
                'reliability': 0.7,
                'published': pub_date.isoformat(),
                'type': 'gnews'
            }

            if news_item['title']:
                all_news.append(news_item)

        unique_news = []
        seen_titles = set()

        for article in all_news:
            simple_title = ''.join(c.lower() for c in article['title'] if c.isalnum())
            is_duplicate = False
            for existing_title in seen_titles:
                similarity = SequenceMatcher(None, simple_title, existing_title).ratio()
                if similarity > 0.8:
                    is_duplicate = True
                    break

            if not is_duplicate:
                seen_titles.add(simple_title)

                try:
                    text = f"{article['title']} {article['description']}"
                    processed_text = pipeline.transform([text])
                    prediction, confidence = ensemble_model.predict(processed_text)

                    article['analysis'] = {
                        'prediction': bool(prediction[0]),
                        'confidence': float(confidence[0])
                    }
                    unique_news.append(article)
                except Exception as e:
                    logger.error(f"Analysis error for article: {str(e)}")
                    continue

        sorted_news = sorted(
            unique_news,
            key=lambda x: (
                x['analysis']['confidence'],
                datetime.fromisoformat(x['published'])
            ),
            reverse=True
        )

        final_news = sorted_news[:15]
        cache_manager.set('news', 'trending', final_news, NEWS_UPDATE_INTERVAL)

        return final_news

    except Exception as e:
        logger.error(f'Error in fetch_trending_news: {str(e)}')
        return []

# Routes
@app.route('/')
def index():
    try:
        return send_from_directory('static', 'index.html')
    except Exception as e:
        logger.error(f"Error serving index page: {str(e)}")
        return jsonify({
            'error': 'Index page not found',
            'message': 'Please ensure index.html exists in the static directory'
        }), 404

@app.route('/api/analyze-text', methods=['POST'])
def analyze_text():
    try:
        data = request.json
        if not data or 'text' not in data:
            return jsonify({'error': 'Text is required'}), 400

        text = data.get('text')
        language = data.get('language', 'en')
        
        processed_text = pipeline.transform([text])
        prediction, confidence = ensemble_model.predict(processed_text)
        
        return jsonify({
            'text': text,
            'analysis': {
                'prediction': bool(prediction[0]),
                'confidence': float(confidence[0])
            },
            'success': True
        })
    except Exception as e:
        logger.error(f'Text analysis error: {str(e)}')
        return jsonify({
            'error': 'Failed to analyze text',
            'details': str(e)
        }), 500

@app.route('/api/analyze-article', methods=['POST'])
def analyze_article():
    data = request.json
    url = data.get('url')
    language = data.get('language', 'en')
    
    if not url:
        return jsonify({'error': 'Article URL is required'}), 400

    try:
        article_text = scrape_article(url)
        
        if not article_text:
            return jsonify({'error': 'Failed to extract article content'}), 400

        processed_text = pipeline.transform([article_text])
        prediction, confidence = ensemble_model.predict(processed_text)
        
        return jsonify({
            'text': article_text,
            'analysis': {
                'prediction': bool(prediction[0]),
                'confidence': float(confidence[0])
            },
            'success': True
        })
    except Exception as e:
        logger.error(f'Article analysis error: {str(e)}')
        return jsonify({
            'error': 'Failed to analyze article',
            'details': str(e)
        }), 500

@app.route('/api/transcribe-recorded', methods=['POST'])
def transcribe_recorded():
    data = request.json
    video_url = data.get('video_url')
    language = data.get('language', 'en')
    
    if not video_url:
        return jsonify({'error': 'Video URL is required'}), 400

    try:
        platform = detect_platform(video_url)
        logger.info(f'Starting transcription for {platform} video: {video_url}')
        
        # Create temporary file for audio
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
            temp_file_path = temp_file.name

        # Download audio using yt-dlp
        process = subprocess.Popen([
            'yt-dlp',
            '-x',
            '--audio-format', 'mp3',
            '--output', temp_file_path,
            '--no-playlist',
            video_url
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            raise Exception(f'Download failed: {stderr.decode()}')

        # Upload to AssemblyAI
        audio_file = aai.transcripts.upload_audio_file(temp_file_path)
        transcript = aai.transcripts.create(
            audio_file,
            language_code=language
        )

        # Process transcript with ML model
        processed_text = pipeline.transform([transcript.text])
        prediction, confidence = ensemble_model.predict(processed_text)

        result = {
            'text': transcript.text,
            'platform': platform,
            'analysis': {
                'prediction': bool(prediction[0]),
                'confidence': float(confidence[0])
            },
            'success': True
        }

        return jsonify(result)

    except Exception as e:
        logger.error(f'Transcription Error: {str(e)}')
        return jsonify({
            'error': 'Failed to transcribe video',
            'details': str(e),
            'platform': detect_platform(video_url)
        }), 500
    finally:
        # Cleanup
        if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)

@app.route('/api/news-stream')
def news_stream():
    def generate():
        while True:
            try:
                news = fetch_trending_news()
                yield f"data: {json.dumps(news)}\n\n"
                time.sleep(NEWS_UPDATE_INTERVAL)
            except Exception as e:
                logger.error(f'News stream error: {str(e)}')
                yield f"data: {json.dumps([])}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }
    )

@app.route('/api/trending-news')
def trending_news():
    try:
        news = fetch_trending_news()
        return jsonify(news)
    except Exception as e:
        logger.error(f'Error fetching trending news: {str(e)}')
        return jsonify({
            'error': 'Failed to fetch trending news',
            'details': str(e)
        }), 500

@app.route('/api/news-sources')
def news_sources():
    sources = [{'name': source['name'], 'url': source['url']} 
              for source in NEWS_SOURCES['RSS_FEEDS']]
    sources.append({'name': 'GNews', 'url': 'https://gnews.io/'})
    return jsonify(sources)

@app.route('/api/clear-cache', methods=['POST'])
def clear_cache():
    try:
        cache_manager.cleanup()
        return jsonify({'message': 'Cache cleared successfully'})
    except Exception as e:
        return jsonify({
            'error': 'Failed to clear cache',
            'details': str(e)
        }), 500

@app.route('/health')
def health_check():
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'cache_stats': cache_manager.get_stats()
    })

# WebSocket Handlers
active_streams = {}

@socketio.on('connect')
def handle_connect():
    logger.info(f'New WebSocket connection established: {request.sid}')
    socketio.emit('status', {
        'message': 'Connected to transcription service'
    }, room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f'WebSocket connection closed: {request.sid}')
    if request.sid in active_streams:
        active_streams[request.sid].stop()
        del active_streams[request.sid]

@socketio.on('start_live')
def handle_start_live(data):
    try:
        url = data.get('url')
        if not url:
            raise ValueError('URL is required')

        # Stop existing stream if any
        if request.sid in active_streams:
            active_streams[request.sid].stop()

        platform = detect_platform(url)
        logger.info(f'Starting live stream processing for {platform}: {url}')

        # Create new stream processor
        stream_processor = StreamProcessor(request.sid)
        active_streams[request.sid] = stream_processor

        # Start yt-dlp process
        process = subprocess.Popen([
            'yt-dlp',
            '-x',
            '--audio-format', 'mp3',
            '--output', '-',
            '--no-playlist',
            '--live-from-start',
            url
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def process_stream():
            try:
                while True:
                    chunk = process.stdout.read(1024)
                    if not chunk:
                        break
                    stream_processor.process_chunk(chunk, len(chunk))
            except Exception as e:
                logger.error(f'Stream processing error: {str(e)}')
                socketio.emit('error', {
                    'error': 'Stream processing failed',
                    'details': str(e)
                }, room=request.sid)
            finally:
                process.terminate()
                if request.sid in active_streams:
                    active_streams[request.sid].stop()
                    del active_streams[request.sid]

        # Start processing in background thread
        thread = threading.Thread(target=process_stream)
        thread.daemon = True
        thread.start()

        socketio.emit('status', {
            'message': f'Live stream processing started for {platform}',
            'platform': platform
        }, room=request.sid)

    except Exception as e:
        logger.error(f'WebSocket message error: {str(e)}')
        socketio.emit('error', {
            'error': 'Failed to process message',
            'details': str(e)
        }, room=request.sid)

# Error Handler
@app.errorhandler(Exception)
def handle_error(error):
    logger.error(f'Server Error: {str(error)}')
    return jsonify({
        'error': 'Internal Server Error',
        'details': str(error)
    }), 500

# Periodic cache cleanup
def cleanup_cache():
    while True:
        try:
            time.sleep(CACHE_CLEANUP_INTERVAL)
            cache_manager.cleanup()
        except Exception as e:
            logger.error(f'Cache cleanup error: {str(e)}')

cleanup_thread = threading.Thread(target=cleanup_cache)
cleanup_thread.daemon = True
cleanup_thread.start()

# Graceful Shutdown
def graceful_shutdown(signum, frame):
    logger.info('Initiating graceful shutdown...')
    
    # Stop all active streams
    for sid, stream in active_streams.items():
        try:
            stream.stop()
        except Exception as e:
            logger.error(f'Error stopping stream {sid}: {str(e)}')
    
    # Clear all caches
    cache_manager.clear()
    
    # Exit
    os._exit(0)

import signal
signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

if __name__ == '__main__':
    try:
        # Create necessary directories if they don't exist
        Path('saved_models').mkdir(exist_ok=True)
        Path('public').mkdir(exist_ok=True)
        
        # Create a basic index.html if it doesn't exist
        index_path = Path('public/index.html')
        if not index_path.exists():
            with open(index_path, 'w') as f:
                f.write("""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>News Analysis Service</title>
                </head>
                <body>
                    <h1>News Analysis Service</h1>
                    <p>API is running successfully.</p>
                </body>
                </html>
                """)
 
        logger.info(f'Server running at http://localhost:{PORT}')
        socketio.run(app, host='0.0.0.0', port=PORT, debug=False)
    except Exception as e:
        logger.error(f'Server startup error: {str(e)}')
        raise