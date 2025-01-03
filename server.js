require('dotenv').config();
const express = require('express');
const bodyParser = require('body-parser');
const WebSocket = require('ws');
const { AssemblyAI } = require('assemblyai');
const { spawn } = require('child_process');
const stream = require('stream');
const path = require('path');
const cors = require('cors');
const axios = require('axios');
const feedparser = require('feedparser-promised');
const winston = require('winston');
const stringSimilarity = require('string-similarity');
const cheerio = require('cheerio');

// Constants and Configurations
const PORT = process.env.PORT || 3000;
const NEWS_UPDATE_INTERVAL = 300000; // 5 minutes
const CACHE_CLEANUP_INTERVAL = 3600000; // 1 hour

// Initialize Express and WebSocket server
const app = express();
const server = require('http').createServer(app);
const wss = new WebSocket.Server({ server });

// Configure logging with Winston
const logger = winston.createLogger({
    level: 'info',
    format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.json()
    ),
    transports: [
        new winston.transports.Console(),
        new winston.transports.File({ filename: 'error.log', level: 'error' }),
        new winston.transports.File({ filename: 'combined.log' })
    ]
});

// News Sources Configuration
const NEWS_SOURCES = {
    RSS_FEEDS: [
        {
            url: 'https://timesofindia.indiatimes.com/rssfeedstopstories.cms',
            name: 'Times of India',
            reliability: 0.8
        },
        {
            url: 'https://www.thehindu.com/news/national/feeder/default.rss',
            name: 'The Hindu',
            reliability: 0.85
        }
    ],
    GNEWS: {
        endpoint: 'https://gnews.io/api/v4/top-headlines',
        params: {
            country: 'in',
            lang: 'en',
            max: 10,
            token: process.env.GNEWS_API_KEY
        }
    }
};

// Optimized Cache System
class CacheManager {
    constructor() {
        this.caches = new Map();
        this.initializeCaches();
    }

    initializeCaches() {
        ['audio', 'news', 'analysis', 'liveStreams', 'analyzers'].forEach(cacheType => {
            this.caches.set(cacheType, new Map());
        });
    }

    get(type, key) {
        const cache = this.caches.get(type);
        const item = cache.get(key);
        if (item && !this.isExpired(item.timestamp)) {
            return item.data;
        }
        return null;
    }

    set(type, key, data, ttl = 3600000) {
        const cache = this.caches.get(type);
        cache.set(key, {
            data,
            timestamp: Date.now(),
            ttl
        });
    }

    delete(type, key) {
        const cache = this.caches.get(type);
        cache.delete(key);
    }

    clear(type) {
        if (type) {
            this.caches.get(type).clear();
        } else {
            this.caches.forEach(cache => cache.clear());
        }
    }

    isExpired(timestamp, ttl = 3600000) {
        return (Date.now() - timestamp) > ttl;
    }

    cleanup() {
        this.caches.forEach((cache, type) => {
            for (const [key, value] of cache.entries()) {
                if (this.isExpired(value.timestamp, value.ttl)) {
                    this.delete(type, key);
                }
            }
        });
    }
}

// Initialize Cache
const cacheManager = new CacheManager();

class CredibilityAnalyzer {
    constructor() {
        this.pythonProcess = null;
        this.retryAttempts = 3;
        this.retryDelay = 2000;
        this.initializePythonServer();
    }

    async initializePythonServer() {
        // Start the Flask server if it's not already running
        this.pythonProcess = spawn('python', ['app.py'], {
            stdio: ['pipe', 'pipe', 'pipe']
        });

        // Wait for Flask server to be ready
        await this.waitForFlaskServer();
    }

    async waitForFlaskServer() {
        for (let i = 0; i < this.retryAttempts; i++) {
            try {
                await fetch('http://localhost:5000/health');
                logger.info('Flask server is ready');
                return;
            } catch (error) {
                if (i < this.retryAttempts - 1) {
                    logger.info(`Waiting for Flask server, attempt ${i + 1}`);
                    await new Promise(resolve => setTimeout(resolve, this.retryDelay));
                }
            }
        }
        throw new Error('Flask server failed to start');
    }

    async analyze(text, metadata = {}, attempt = 0) {
        try {
            const response = await fetch('http://localhost:5000/predict', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            if (attempt < this.retryAttempts) {
                logger.info(`Retrying analysis, attempt ${attempt + 1}`);
                await new Promise(resolve => setTimeout(resolve, this.retryDelay));
                return this.analyze(text, metadata, attempt + 1);
            }
            return this.fallbackAnalysis(text, metadata);
        }
    }
}

// Optimized Buffer Management
class OptimizedSlidingBuffer {
    constructor(maxDuration = 60000, chunkSize = 10000) {
        this.maxDuration = maxDuration;
        this.chunkSize = chunkSize;
        this.chunks = [];
        this.totalDuration = 0;
        this.lastProcessedTimestamp = 0;
    }

    addChunk(chunk, duration) {
        const now = Date.now();
        this.chunks.push({ 
            chunk, 
            duration,
            timestamp: now 
        });
        this.totalDuration += duration;

        while (this.totalDuration > this.maxDuration) {
            const removed = this.chunks.shift();
            this.totalDuration -= removed.duration;
        }

        return this.shouldProcess(now);
    }

    shouldProcess(now) {
        return (now - this.lastProcessedTimestamp) >= this.chunkSize;
    }

    getBuffer() {
        this.lastProcessedTimestamp = Date.now();
        return Buffer.concat(this.chunks.map(c => c.chunk));
    }

    clear() {
        this.chunks = [];
        this.totalDuration = 0;
    }
}

// Initialize AssemblyAI
const assemblyAI = new AssemblyAI({
    apiKey: process.env.ASSEMBLYAI_API_KEY
});

// Optimized Stream Processor
class StreamProcessor {
    constructor(assemblyAI, ws) {
        this.assemblyAI = assemblyAI;
        this.ws = ws;
        this.buffer = new OptimizedSlidingBuffer();
        this.analyzer = new CredibilityAnalyzer();
        this.transcriptionQueue = [];
        this.isProcessing = false;
    }

    async processChunk(chunk, duration) {
        if (!this.buffer.addChunk(chunk, duration)) {
            return;
        }

        this.transcriptionQueue.push({
            buffer: this.buffer.getBuffer(),
            timestamp: Date.now()
        });

        if (!this.isProcessing) {
            this.processQueue();
        }
    }

    async processQueue() {
        if (this.isProcessing || this.transcriptionQueue.length === 0) {
            return;
        }

        this.isProcessing = true;
        const { buffer, timestamp } = this.transcriptionQueue.shift();

        try {
            const audioStream = new stream.PassThrough();
            audioStream.end(buffer);

            const [audioFile] = await Promise.all([
                this.assemblyAI.files.upload(audioStream, {
                    fileName: `chunk-${timestamp}.mp3`,
                    contentType: 'audio/mp3'
                })
            ]);

            const transcript = await this.assemblyAI.transcripts.transcribe({
                audio: audioFile,
                language_code: 'en'
            });

            const analysis = await this.analyzer.analyze(transcript.text, {
                type: 'live_stream',
                timestamp
            });

            this.ws.send(JSON.stringify({
                type: 'transcription',
                text: transcript.text,
                analysis,
                timestamp
            }));

        } catch (error) {
            logger.error('Stream processing error:', error);
            this.ws.send(JSON.stringify({
                type: 'error',
                error: 'Stream processing failed',
                details: error.message
            }));
        } finally {
            this.isProcessing = false;
            if (this.transcriptionQueue.length > 0) {
                this.processQueue();
            }
        }
    }
}

// Utility Functions
const detectPlatform = (url) => {
    try {
        const urlObj = new URL(url);
        const domain = urlObj.hostname.toLowerCase();
        
        const platformMap = {
            'youtube.com': 'youtube',
            'youtu.be': 'youtube',
            'instagram.com': 'instagram',
            'facebook.com': 'facebook',
            'fb.com': 'facebook',
            'tiktok.com': 'tiktok',
            'twitter.com': 'twitter',
            'vimeo.com': 'vimeo'
        };

        for (const [key, value] of Object.entries(platformMap)) {
            if (domain.includes(key)) return value;
        }
        
        return 'unknown';
    } catch (error) {
        throw new Error('Invalid URL format');
    }
};

// News Fetching Functions
const fetchGNewsArticles = async () => {
    try {
        const apiKey = process.env.GNEWS_API_KEY;
        if (!apiKey) {
            logger.warn('GNews API key is not configured');
            return [];
        }

        // Validate API key format
        if (!apiKey.match(/^[a-zA-Z0-9]{32}$/)) {
            logger.error('Invalid GNews API key format');
            return [];
        }

        const response = await axios.get(NEWS_SOURCES.GNEWS.endpoint, {
            params: {
                ...NEWS_SOURCES.GNEWS.params,
                token: apiKey
            },
            validateStatus: status => status === 200
        });

        return response.data?.articles || [];
    } catch (error) {
        if (error.response?.status === 401) {
            logger.error('Invalid GNews API key - please check your credentials');
        } else {
            logger.error(`GNews API Error: ${error.message}`);
        }
        return [];
    }
};

const fetchTrendingNews = async () => {
    const cachedNews = cacheManager.get('news', 'trending');
    if (cachedNews) return cachedNews;

    try {
        const [rssResults, gnewsResults] = await Promise.all([
            Promise.all(NEWS_SOURCES.RSS_FEEDS.map(async (source) => {
                try {
                    const items = await feedparser.parse(source.url);
                    return items.slice(0, 10).map(item => ({
                        title: item.title,
                        description: item.description || item.summary,
                        url: item.link,
                        source: source.name,
                        reliability: source.reliability,
                        published: item.pubDate
                    }));
                } catch (error) {
                    logger.error(`RSS feed error for ${source.name}:`, error);
                    return [];
                }
            })).then(results => results.flat()),
            fetchGNewsArticles()
        ]);

        const analyzer = new CredibilityAnalyzer();
        const allNews = [...rssResults, ...gnewsResults];

        const uniqueNews = [];
        for (const current of allNews) {
            const isDuplicate = uniqueNews.some(item => 
                stringSimilarity.compareTwoStrings(item.title, current.title) > 0.8
            );
            if (!isDuplicate) {
                const analysis = await analyzer.analyze(
                    `${current.title} ${current.description || ''}`,
                    { source: current.source, published: current.published }
                );
                uniqueNews.push({ ...current, analysis });
            }
        }

        const sortedNews = uniqueNews.sort((a, b) => {
            const timeWeight = 0.3;
            const credibilityWeight = 0.7;
            
            const timeA = new Date(a.published).getTime();
            const timeB = new Date(b.published).getTime();
            
            const timeScore = (timeA - timeB) * timeWeight;
            const credScore = (a.analysis.confidence - b.analysis.confidence) * credibilityWeight;
            
            return (credScore + timeScore) * -1;
        });

        const result = sortedNews.slice(0, 15);
        cacheManager.set('news', 'trending', result, NEWS_UPDATE_INTERVAL);
        return result;
    } catch (error) {
        logger.error('Error in fetchTrendingNews:', error);
        return [];
    }
};

// Article Scraping Function
async function scrapeArticle(url) {
    try {
        const response = await axios.get(url, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        });
        
        const $ = cheerio.load(response.data);
        
        $('script, style, nav, header, footer, .ads, #comments').remove();
        
        const article = $('article').length ? 
            $('article') : 
            $('main').length ? 
                $('main') : 
                $('body');
                
        const paragraphs = [];
        article.find('p').each((_, element) => {
            const text = $(element).text().trim();
            if (text.length > 30) {
                paragraphs.push(text);
            }
        });
        
        return paragraphs.join(' ');
    } catch (error) {
        logger.error("Error scraping article:", error);
        return null;
    }
}

// Express Middleware
app.use(cors());
// Express Middleware (continued)
app.use(bodyParser.json());
app.use(express.static(path.join(__dirname, 'public')));

// API Routes
app.post('/api/analyze-article', async (req, res) => {
    const { url, language = 'en' } = req.body;

    if (!url) {
        return res.status(400).json({ error: 'Article URL is required' });
    }

    try {
        const articleText = await scrapeArticle(url);
        
        if (!articleText) {
            throw new Error('Failed to extract article content');
        }

        const analyzer = new CredibilityAnalyzer();
        const analysis = await analyzer.analyze(articleText, {
            type: 'article',
            url,
            language
        });

        res.json({
            text: articleText,
            analysis,
            success: true
        });
    } catch (error) {
        logger.error('Article analysis error:', error);
        res.status(500).json({
            error: 'Failed to analyze article',
            details: error.message
        });
    }
});

app.post('/api/analyze-text', async (req, res) => {
    const { text, language = 'en' } = req.body;

    if (!text) {
        return res.status(400).json({ error: 'Text is required' });
    }

    try {
        const analyzer = new CredibilityAnalyzer();
        const analysis = await analyzer.analyze(text, {
            type: 'manual_input',
            language
        });

        res.json({
            text,
            analysis,
            success: true
        });
    } catch (error) {
        logger.error('Text analysis error:', error);
        res.status(500).json({
            error: 'Failed to analyze text',
            details: error.message
        });
    }
});

app.post('/api/transcribe-recorded', async (req, res) => {
    const { video_url, language = 'en' } = req.body;
    let ytDlpProcess = null;

    if (!video_url) {
        return res.status(400).json({ error: 'Video URL is required' });
    }

    try {
        const platform = detectPlatform(video_url);
        logger.info(`Starting transcription for ${platform} video: ${video_url}`);
        
        ytDlpProcess = spawn('yt-dlp', [
            '-x',
            '--audio-format', 'mp3',
            '--output', '-',
            '--no-playlist',
            video_url
        ]);

        const audioChunks = [];
        
        ytDlpProcess.stdout.on('data', chunk => {
            audioChunks.push(chunk);
        });

        ytDlpProcess.stderr.on('data', data => {
            logger.info(`Download progress: ${data}`);
        });

        const processVideo = new Promise((resolve, reject) => {
            ytDlpProcess.on('close', code => {
                if (code === 0 && audioChunks.length > 0) {
                    resolve(Buffer.concat(audioChunks));
                } else {
                    reject(new Error(`Download failed with code ${code}`));
                }
            });
            ytDlpProcess.on('error', reject);
        });

        const audioBuffer = await processVideo;
        
        if (!audioBuffer || audioBuffer.length === 0) {
            throw new Error('No audio data received from video');
        }

        const audioStream = new stream.PassThrough();
        audioStream.end(audioBuffer);

        const audioFile = await assemblyAI.files.upload(audioStream, {
            fileName: `${platform}-${Date.now()}.mp3`,
            contentType: 'audio/mp3'
        });

        const transcript = await assemblyAI.transcripts.transcribe({
            audio: audioFile,
            language_code: language
        });

        const analyzer = new CredibilityAnalyzer();
        const analysis = await analyzer.analyze(transcript.text, {
            type: 'recorded_video',
            platform,
            url: video_url
        });

        res.json({
            text: transcript.text,
            platform,
            analysis,
            success: true
        });
    } catch (error) {
        logger.error('Transcription Error:', error);
        res.status(500).json({
            error: 'Failed to transcribe video',
            details: error.message,
            platform: detectPlatform(video_url)
        });
    } finally {
        if (ytDlpProcess && !ytDlpProcess.killed) {
            try {
                ytDlpProcess.kill();
            } catch (err) {
                logger.error('Error killing yt-dlp process:', err);
            }
        }
    }
});

app.get('/api/news-stream', (req, res) => {
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    const sendNews = async () => {
        try {
            const news = await fetchTrendingNews();
            res.write(`data: ${JSON.stringify(news)}\n\n`);
        } catch (error) {
            logger.error('News stream error:', error);
        }
    };

    const newsInterval = setInterval(sendNews, NEWS_UPDATE_INTERVAL);
    sendNews();

    req.on('close', () => {
        clearInterval(newsInterval);
    });
});

app.get('/api/trending-news', async (req, res) => {
    try {
        const news = await fetchTrendingNews();
        res.json(news);
    } catch (error) {
        logger.error('Error fetching trending news:', error);
        res.status(500).json({
            error: 'Failed to fetch trending news',
            details: error.message
        });
    }
});

app.get('/api/news-sources', (req, res) => {
    const sources = NEWS_SOURCES.RSS_FEEDS.map(source => ({
        name: source.name,
        url: source.url
    }));
    sources.push({ name: 'GNews', url: 'https://gnews.io/' });
    
    res.json(sources);
});

app.post('/api/clear-cache', (req, res) => {
    cacheManager.cleanup();
    res.json({ message: 'Cache cleared successfully' });
});

app.get('/health', (req, res) => {
    res.json({ 
        status: 'ok',
        timestamp: new Date().toISOString(),
        cache_stats: {
            audio: cacheManager.caches.get('audio').size,
            news: cacheManager.caches.get('news').size,
            analysis: cacheManager.caches.get('analysis').size
        }
    });
});



// WebSocket Handler for Live Streaming
wss.on('connection', (ws) => {
    logger.info('New WebSocket connection established');
    let currentStream = null;

    ws.on('message', async (message) => {
        try {
            const data = JSON.parse(message);

            if (data.type === 'start_live') {
                const platform = detectPlatform(data.url);
                logger.info(`Starting live stream processing for ${platform}:`, data.url);

                if (currentStream) {
                    currentStream.kill();
                }

                const ytDlpProcess = spawn('yt-dlp', [
                    '-x',
                    '--audio-format', 'mp3',
                    '--output', '-',
                    '--no-playlist',
                    '--live-from-start',
                    data.url
                ]);

                const streamProcessor = new StreamProcessor(assemblyAI, ws);

                ytDlpProcess.stdout.on('data', chunk => {
                    streamProcessor.processChunk(chunk, 1);
                });

                ytDlpProcess.stderr.on('data', data => {
                    logger.info(`Stream progress: ${data}`);
                });

                currentStream = ytDlpProcess;
                cacheManager.set('liveStreams', `${platform}-${Date.now()}`, currentStream);

                ws.send(JSON.stringify({
                    type: 'status',
                    message: `Live stream processing started for ${platform}`,
                    platform
                }));
            }
        } catch (error) {
            logger.error('WebSocket message error:', error);
            ws.send(JSON.stringify({
                type: 'error',
                error: 'Failed to process message',
                details: error.message
            }));
        }
    });

    ws.on('close', () => {
        logger.info('WebSocket connection closed');
        if (currentStream) {
            currentStream.kill();
        }
    });

    ws.send(JSON.stringify({
        type: 'status',
        message: 'Connected to transcription service'
    }));
});

// Error Handler
app.use((err, req, res, next) => {
    logger.error('Server Error:', err);
    res.status(500).json({
        error: 'Internal Server Error',
        details: err.message
    });
});

// Periodic Cache Cleanup
setInterval(() => {
    cacheManager.cleanup();
}, CACHE_CLEANUP_INTERVAL);

// Graceful Shutdown
const gracefulShutdown = async () => {
    logger.info('Initiating graceful shutdown...');
    
    // Clean up any active analyzers
    const activeAnalyzers = Array.from(cacheManager.caches.get('analyzers').values());
    await Promise.all(activeAnalyzers.map(analyzer => analyzer.cleanup()));
    
    cacheManager.caches.get('liveStreams').forEach(stream => stream.kill());
    cacheManager.clear();
    
    server.close(() => {
        logger.info('Server shut down complete');
        process.exit(0);
    });
};

process.on('SIGTERM', gracefulShutdown);
process.on('SIGINT', gracefulShutdown);

// Start Server
server.listen(PORT, () => {
    logger.info(`Server running at http://localhost:${PORT}`);
});

module.exports = { app, server };