# sampleapp.py code
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
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import assemblyai as aai
from dotenv import load_dotenv
import queue
import concurrent.futures
from pathlib import Path
import shutil

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__, static_folder='public')
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



class AudioProcessor:
    def __init__(self):
        self.sample_rate = 16000
        self.sample_width = 2
        self.channels = 1

    def normalize_audio(self, audio_data, target_sample_rate=16000):
        # Convert to mono and normalize sample rate
        with wave.open(io.BytesIO(audio_data), 'rb') as wav:
            n_channels = wav.getnchannels()
            sampwidth = wav.getsampwidth()
            framerate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())

            # Convert to mono if necessary
            if n_channels == 2:
                frames = audioop.tomono(frames, sampwidth, 0.5, 0.5)

            # Resample if necessary
            if framerate != target_sample_rate:
                frames = audioop.ratecv(frames, sampwidth, 1, framerate, 
                                      target_sample_rate, None)[0]

            return frames
# Stream Processor
class StreamProcessor:
    def __init__(self, socket_id):
        self.socket_id = socket_id
        self.audio_buffer = io.BytesIO()
        self.buffer_lock = threading.Lock()
        self.is_processing = False
        self.should_stop = False
        self.audio_processor = AudioProcessor()
        self.transcriber = aai.Transcriber()
        self.processing_queue = queue.Queue()
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.last_processed = 0
        self.temp_files = []
        self.min_audio_length = MIN_AUDIO_LENGTH
        self.start_processing_thread()


    def start_processing_thread(self):
        self.thread_pool.submit(self._process_queue)

    def process_chunk(self, chunk):
        if self.should_stop:
            return

        try:
            with self.buffer_lock:
                self.audio_buffer.write(chunk)
                current_size = self.audio_buffer.tell()

                # Process if buffer has enough data
                if current_size >= CHUNK_SIZE * 10:  # Increased buffer size
                    audio_data = self.audio_buffer.getvalue()
                    self.audio_buffer = io.BytesIO()  # Reset buffer
                    
                    # Normalize audio
                    normalized_audio = self.audio_processor.normalize_audio(audio_data)
                    
                    # Queue for processing
                    self.processing_queue.put({
                        'audio': normalized_audio,
                        'timestamp': time.time()
                    })

        except Exception as e:
            logger.error(f'Error processing chunk: {str(e)}')
            socketio.emit('error', {
                'error': 'Audio processing error',
                'details': str(e)
            }, room=self.socket_id)
    
    def _process_queue(self):
        while not self.should_stop:
            try:
                item = self.processing_queue.get(timeout=1)
                if item:
                    self._process_audio(item['buffer'], item['timestamp'])
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f'Queue processing error: {str(e)}')

    def _process_audio(self, buffer, timestamp):
        temp_file_path = None
        try:
            # Create temp file with unique name
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
                temp_file_path = temp_file.name
                self.temp_files.append(temp_file_path)
                temp_file.write(buffer)
                
                with wave.open(temp_file_path, 'wb') as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(16000)
                    wav_file.writeframes(audio_data)

            # Log the file size for debugging
            file_size = os.path.getsize(temp_file_path)
            if file_size < 1024:  # Skip if too small
                return
            
            config = aai.TranscriptionConfig(
                punctuate=True,
                format_text=True,
                language_detection=True,
                audio_start_from=self.last_processed
            )

            # Transcribe
            transcript = self.transcriber.transcribe(
                temp_file_path,
                config=config
            )

            if transcript and transcript.text:
                # Update last processed timestamp
                self.last_processed = timestamp

                # Process transcribed text
                processed_text = pipeline.transform([transcript.text])
                prediction, confidence = ensemble_model.predict(processed_text)

                # Emit results
                socketio.emit('transcription', {
                    'text': transcript.text,
                    'analysis': {
                        'prediction': bool(prediction[0]),
                        'confidence': float(confidence[0])
                    },
                    'timestamp': timestamp
                }, room=self.socket_id)

        except Exception as e:
            logger.error(f'Audio processing error: {str(e)}')
            socketio.emit('error', {
                'error': 'Audio processing failed',
                'details': str(e)
            }, room=self.socket_id)
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                    self.temp_files.remove(temp_file_path)
                except Exception as e:
                    logger.error(f'Error removing temp file: {str(e)}')

    def stop(self):
        self.should_stop = True
        self.thread_pool.shutdown(wait=False)
        
        # Clear buffer
        with self.buffer_lock:
            self.audio_buffer = io.BytesIO()
        
        # Clean up temp files
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception as e:
                logger.error(f'Error removing temp file during shutdown: {str(e)}')
        self.temp_files.clear()

    def stop(self):
        self.should_stop = True
        self.thread_pool.shutdown(wait=False)
        self.buffer.clear()
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception as e:
                logger.error(f'Error removing temp file during shutdown: {str(e)}')
        self.temp_files.clear()

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
        return send_from_directory('public', 'index.html')
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
        
        # Create temporary directory for audio
        temp_dir = tempfile.mkdtemp()
        temp_file_path = os.path.join(temp_dir, 'audio.mp3')

        try:
            # Download audio using yt-dlp
            process = subprocess.run([
                'yt-dlp',
                '-x',
                '--audio-format', 'mp3',
                '--audio-quality', '0',
                '--output', temp_file_path,
                '--no-playlist',
                '--force-overwrites',
                '--no-warnings',
                video_url
            ], capture_output=True, text=True, check=True)

            # Verify file exists and has size
            if not os.path.exists(temp_file_path) or os.path.getsize(temp_file_path) == 0:
                raise Exception("Failed to download audio file")

            # Initialize AssemblyAI with the API key
            aai.settings.api_key = os.getenv('ASSEMBLYAI_API_KEY')
            
            # Create a transcriber
            transcriber = aai.Transcriber()

            # Transcribe the audio file
            transcript = transcriber.transcribe(temp_file_path)

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

        except subprocess.CalledProcessError as e:
            logger.error(f'yt-dlp error: {e.stderr}')
            return jsonify({
                'error': 'Failed to download video',
                'details': e.stderr,
                'platform': platform
            }), 500
        finally:
            # Cleanup with proper error handling
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception as cleanup_error:
                logger.error(f'Error cleaning up temp files: {str(cleanup_error)}')

    except Exception as e:
        logger.error(f'Transcription Error: {str(e)}')
        return jsonify({
            'error': 'Failed to transcribe video',
            'details': str(e),
            'platform': detect_platform(video_url)
        }), 500   
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

        # Configure yt-dlp command with better options
        command = [
            'yt-dlp',
            '-x',
            '--audio-format', 'wav',
            '--audio-quality', '0',
            '--output', '-',
            '--no-playlist',
            '--live-from-start',
            '--force-ipv4',  # More stable connection
            '--retries', '3',  # Retry on failure
            '--buffer-size', '16K',  # Larger buffer
            url
        ]

        # Start yt-dlp process
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=CHUNK_SIZE
        )

        def process_stream():
            try:
                def monitor_errors():
                    for line in process.stderr:
                        error_msg = line.decode().strip()
                        if error_msg:
                            logger.error(f'yt-dlp error: {error_msg}')
                            socketio.emit('error', {
                                'error': 'Stream error',
                                'details': error_msg
                            }, room=request.sid)

                # Start error monitoring thread
                error_thread = threading.Thread(target=monitor_errors)
                error_thread.daemon = True
                error_thread.start()

                # Process audio stream
                while True:
                    chunk = process.stdout.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    stream_processor.process_chunk(chunk)

                    # Check process status
                    if process.poll() is not None:
                        error_output = process.stderr.read()
                        if error_output:
                            logger.error(f'yt-dlp process ended with error: {error_output.decode()}')
                        break

            except Exception as e:
                logger.error(f'Stream processing error: {str(e)}')
                socketio.emit('error', {
                    'error': 'Stream processing failed',
                    'details': str(e)
                }, room=request.sid)
            finally:
                try:
                    process.terminate()
                except:
                    pass
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