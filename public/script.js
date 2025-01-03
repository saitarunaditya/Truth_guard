// server side  Configuration
const CONFIG = {
    WS_URL: 'ws://localhost:3000',
    API_URL: 'http://localhost:3000',
    MAX_RECONNECT_ATTEMPTS: 5,
    RECONNECT_DELAY: 2000,
    ERROR_DISPLAY_DURATION: 5000
};

// DOM Elements Management
const elements = (() => {
    const get = (id) => document.getElementById(id);
    
    return {
        form: get('transcriptionForm'),
        videoType: get('videoType'),
        videoUrl: get('videoUrl'),
        language: get('language'),
        startButton: get('startTranscription'),
        stopButton: get('stopTranscription'),
        loadingMessage: get('loadingMessage'),
        transcriptionResult: get('transcriptionResult'),
        errorMessage: get('errorMessage'),
        statusMessage: get('statusMessage'),
        factCheckResult: get('factCheckResult'),
        trendingNews: get('trendingNews'),
        textInput: get('textInput'),
        articleUrl: get('articleUrl'),
        confidenceChart: get('confidenceChart'),
        refreshNews: get('refreshNews')
    };
})();


// State Management
const state = {
    ws: null,
    isLiveTranscribing: false,
    reconnectAttempts: 0,
    currentChartInstance: null,
    newsEventSource: null
};

const UI = {
    showError(message, duration = CONFIG.ERROR_DISPLAY_DURATION) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'fixed bottom-4 right-4 bg-red-500 text-white px-4 py-2 rounded-lg shadow-lg text-sm sm:text-base z-50 animate-fade-in';
        errorDiv.textContent = message;
        document.body.appendChild(errorDiv);

        setTimeout(() => {
            errorDiv.classList.add('animate-fade-out');
            setTimeout(() => errorDiv.remove(), 300);
        }, duration);
    },

    showStatus(message, isError = false) {
        const statusDiv = document.createElement('div');
        statusDiv.className = `fixed top-4 right-4 px-4 py-2 rounded-lg shadow-lg text-sm sm:text-base z-50 animate-fade-in ${
            isError ? 'bg-red-100 text-red-700' : 'bg-green-100 text-green-700'
        }`;
        statusDiv.textContent = message;
        document.body.appendChild(statusDiv);

        setTimeout(() => {
            statusDiv.classList.add('animate-fade-out');
            setTimeout(() => statusDiv.remove(), 300);
        }, 3000);
    },

    showLoading(show) {
        elements.loadingMessage.classList.toggle('hidden', !show);
        elements.startButton.disabled = show;
        if (show) {
            elements.startButton.classList.add('opacity-50', 'cursor-not-allowed');
        } else {
            elements.startButton.classList.remove('opacity-50', 'cursor-not-allowed');
        }
    },

    updateTranscription(text, append = false) {
        if (append) {
            const newParagraph = document.createElement('p');
            newParagraph.textContent = text;
            newParagraph.className = 'mb-2 p-2 bg-white rounded text-sm sm:text-base';
            elements.transcriptionResult.appendChild(newParagraph);
            elements.transcriptionResult.scrollTop = elements.transcriptionResult.scrollHeight;
        } else {
            elements.transcriptionResult.innerHTML = `<p class="mb-2 p-2 bg-white rounded text-sm sm:text-base">${text}</p>`;
        }
    },

    clearTranscription() {
        elements.transcriptionResult.innerHTML = '';
        elements.factCheckResult.innerHTML = '';
        if (state.currentChartInstance) {
            Plotly.purge(elements.confidenceChart);
            state.currentChartInstance = null;
        }
    },

    updateFactCheck(analysis) {
        if (!analysis) return;

        const { confidence, prediction, detailed_analysis } = analysis;

        const getConfidenceClass = (score) => {
            return score > 70 ? 'bg-green-100 text-green-600' :
                   score > 50 ? 'bg-yellow-100 text-yellow-600' :
                   'bg-red-100 text-red-600';
        };

        elements.factCheckResult.innerHTML = `
            <div class="space-y-4">
                <div class="p-4 rounded-lg ${getConfidenceClass(confidence * 100)}">
                    <h3 class="font-bold text-lg mb-2">Content Analysis</h3>
                    <div class="flex flex-col gap-2">
                        <div class="flex justify-between items-center">
                            <span class="font-medium">Credibility Score:</span>
                            <span class="font-bold">${(confidence * 100).toFixed(2)}%</span>
                        </div>
                        <div class="text-sm font-medium">
                            Prediction: ${prediction}
                        </div>
                    </div>
                </div>
                ${detailed_analysis ? this.generateDetailedAnalysis(detailed_analysis) : ''}
            </div>
        `;
        
        this.createConfidenceChart(confidence);
    },

    generateDetailedAnalysis(detailed_analysis) {
        return `
            <div class="p-4 bg-white rounded-lg shadow-sm">
                <h4 class="font-semibold mb-3">Analysis Details</h4>
                <div class="space-y-3">
                    <div class="text-sm">
                        <p class="font-medium">Facts vs Claims</p>
                        <div class="mt-1 flex justify-between">
                            <span>Factual Statements: ${detailed_analysis.statements.factual.length}</span>
                            <span>Claims: ${detailed_analysis.statements.claims.length}</span>
                        </div>
                    </div>
                    <div class="text-sm">
                        <p class="font-medium">Writing Style</p>
                        <div class="mt-1">
                            <div>Academic Style: ${detailed_analysis.style.academic_style}/10</div>
                            <div>Emotional Language: ${detailed_analysis.style.emotional_language} instances</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    createConfidenceChart(confidence) {
        if (!elements.confidenceChart) return;

        const data = [{
            type: 'indicator',
            mode: 'gauge+number',
            value: 0, // Start at 0 for animation
            gauge: {
                axis: { range: [0, 100] },
                bar: { color: `hsl(${confidence * 1.2}, 70%, 50%)` },
                bgcolor: 'white',
                borderwidth: 2,
                bordercolor: '#ddd',
                steps: [
                    { range: [0.0, 50], color: '#fee2e2' },
                    { range: [50, 70], color: '#fef3c7' },
                    { range: [70, 100], color: '#dcfce7' }
                ],
                threshold: {
                    line: { color: 'black', width: 4 },
                    thickness: 0.75,
                    value: confidence
                }
            }
        }];

        const layout = {
            width: elements.confidenceChart.offsetWidth,
            height: 250,
            margin: { t: 25, r: 25, l: 25, b: 25 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            font: { size: 12 },
            transition: {
                duration: 1000,
                easing: 'cubic-in-out'
            }
        };

        const config = {
            responsive: true,
            displayModeBar: false
        };

        if (state.currentChartInstance) {
            Plotly.purge(elements.confidenceChart);
        }

        Plotly.newPlot(elements.confidenceChart, data, layout, config)
            .then(() => {
                // Animate to final value
                Plotly.animate(elements.confidenceChart, {
                    data: [{ value: confidence * 100 }],
                    traces: [0],
                    layout: {}
                }, {
                    transition: {
                        duration: 1000,
                        easing: 'cubic-in-out'
                    },
                    frame: {
                        duration: 1000,
                        redraw: false
                    }
                });
                state.currentChartInstance = elements.confidenceChart;
            });
    },

    updateTrendingNews(news) {
        elements.trendingNews.innerHTML = news.map(article => `
            <div class="mb-3 p-3 bg-white rounded-lg shadow-sm border border-gray-200">
                <div class="flex flex-col sm:flex-row sm:justify-between sm:items-start gap-2">
                    <h3 class="text-base font-semibold">${article.title}</h3>
                    <span class="text-xs text-gray-500">
                        ${new Date(article.published).toLocaleDateString()}
                    </span>
                </div>
                <p class="text-sm text-gray-600 mt-2">
                    ${article.description || ''}
                </p>
                <div class="mt-3 flex justify-between items-center">
                    <span class="text-xs font-medium ${
                        article.analysis.confidence > 0.70 ? 'text-green-600' : 
                        article.analysis.confidence > 0.50 ? 'text-yellow-600' : 'text-red-600'
                    }">
                        Credibility: ${article.analysis.confidence*100}%
                    </span>
                    <a href="${article.url}" target="_blank" 
                       class="text-blue-500 hover:underline text-xs">
                        Read more →
                    </a>
                </div>
            </div>
        `).join('');
    }
};



// WebSocket Management
const WebSocketManager = {
    setup() {
        if (state.ws) {
            state.ws.close();
        }

        state.ws = new WebSocket(CONFIG.WS_URL);
        this.attachEventListeners();
    },

    attachEventListeners() {
        state.ws.onopen = () => {
            console.log('WebSocket connection established');
            UI.showStatus('Connected to transcription service');
            state.reconnectAttempts = 0;
        };

        state.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (error) {
                console.error('Error processing WebSocket message:', error);
                UI.showError('Error processing transcription data');
            }
        };

        state.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            UI.showError('Connection error. Please try again.');
        };

        state.ws.onclose = () => {
            console.log('WebSocket connection closed');
            this.handleDisconnect();
        };
    },

    handleMessage(data) {
        switch (data.type) {
            case 'transcription':
                UI.updateTranscription(data.text, true);
                if (data.analysis) {
                    UI.updateFactCheck(data.analysis);
                }
                break;
            case 'status':
                UI.showStatus(data.message);
                break;
            case 'error':
                UI.showError(data.error);
                break;
        }
    },

    handleDisconnect() {
        if (state.isLiveTranscribing && state.reconnectAttempts < CONFIG.MAX_RECONNECT_ATTEMPTS) {
            state.reconnectAttempts++;
            UI.showStatus(
                `Connection lost. Attempting to reconnect... (${state.reconnectAttempts}/${CONFIG.MAX_RECONNECT_ATTEMPTS})`,
                true
            );
            setTimeout(() => this.setup(), CONFIG.RECONNECT_DELAY * state.reconnectAttempts);
        } else if (state.reconnectAttempts >= CONFIG.MAX_RECONNECT_ATTEMPTS) {
            UI.showError('Maximum reconnection attempts reached. Please refresh the page.');
            TranscriptionManager.stopLive();
        }
    }
};

// API Functions
const API = {
    async transcribeRecorded(videoUrl, language) {
        const response = await fetch(`${CONFIG.API_URL}/api/transcribe-recorded`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_url: videoUrl, language })
        });

        if (!response.ok) {
            throw new Error('Failed to transcribe video');
        }

        return response.json();
    },

    async analyzeArticle(url, language) {
        const response = await fetch(`${CONFIG.API_URL}/api/analyze-article`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, language })
        });

        if (!response.ok) {
            throw new Error('Failed to analyze article');
        }

        return response.json();
    },

    async analyzeText(text, language) {
        const response = await fetch(`${CONFIG.API_URL}/api/analyze-text`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, language })
        });

        if (!response.ok) {
            throw new Error('Failed to analyze text');
        }

        return response.json();
    },

    async fetchTrendingNews() {
        const response = await fetch(`${CONFIG.API_URL}/api/trending-news`);
        if (!response.ok) {
            throw new Error('Failed to fetch trending news');
        }
        return response.json();
    },

    setupNewsStream() {
        if (state.newsEventSource) {
            state.newsEventSource.close();
        }

        state.newsEventSource = new EventSource(`${CONFIG.API_URL}/api/news-stream`);
        
        state.newsEventSource.onmessage = (event) => {
            try {
                const news = JSON.parse(event.data);
                UI.updateTrendingNews(news);
            } catch (error) {
                console.error('Error processing news stream:', error);
            }
        };

        state.newsEventSource.onerror = (error) => {
            console.error('News stream error:', error);
            state.newsEventSource.close();
            setTimeout(() => this.setupNewsStream(), CONFIG.RECONNECT_DELAY);
        };
    }
};

// Transcription Management
const TranscriptionManager = {
    async handleRecorded(videoUrl, language) {
        try {
            const data = await API.transcribeRecorded(videoUrl, language);
            UI.updateTranscription(data.text);
            UI.updateFactCheck(data.analysis);
            UI.showStatus('Transcription completed successfully');
        } catch (error) {
            console.error('Transcription error:', error);
            UI.showError(error.message);
        } finally {
            UI.showLoading(false);
        }
    },

    async handleArticleAnalysis(url, language) {
        try {
            const data = await API.analyzeArticle(url, language);
            UI.updateTranscription(data.text);
            UI.updateFactCheck(data.analysis);
            UI.showStatus('Article analysis completed successfully');
        } catch (error) {
            console.error('Article analysis error:', error);
            UI.showError(error.message);
        } finally {
            UI.showLoading(false);
        }
    },

    async handleTextAnalysis(text, language) {
        try {
            const data = await API.analyzeText(text, language);
            UI.updateTranscription(data.text);
            UI.updateFactCheck(data.analysis);
            UI.showStatus('Text analysis completed successfully');
        } catch (error) {
            console.error('Text analysis error:', error);
            UI.showError(error.message);
        } finally {
            UI.showLoading(false);
        }
    },

    startLive(videoUrl, language) {
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
            WebSocketManager.setup();
        }

        state.isLiveTranscribing = true;
        elements.stopButton.classList.remove('hidden');
        elements.startButton.classList.add('hidden');

        state.ws.send(JSON.stringify({
            type: 'start_live',
            url: videoUrl,
            language
        }));

        UI.showStatus('Live transcription started');
    },

    stopLive() {
        state.isLiveTranscribing = false;
        elements.stopButton.classList.add('hidden');
        elements.startButton.classList.remove('hidden');

        if (state.ws) {
            state.ws.close();
            state.ws = null;
        }

        UI.showStatus('Live transcription stopped');
    }
};

// Input validation and helper functions
const Validators = {
    isValidUrl(string) {
        try {
            new URL(string);
            return true;
        } catch (_) {
            return false;
        }
    },

    isValidText(text) {
        return text.trim().length > 0;
    },

    getInputErrorMessage(type) {
        switch (type) {
            case 'text':
                return 'Please enter text for analysis';
            case 'article':
                return 'Please enter a valid article URL';
            default:
                return 'Please enter a valid URL';
        }
    }
};

// Event handlers
const EventHandlers = {
    handleVideoTypeChange(e) {
        const type = e.target.value;
        const urlContainer = document.getElementById('urlContainer');
        const textContainer = document.getElementById('textContainer');
        const articleContainer = document.getElementById('articleContainer');
        
        // Hide all containers first
        urlContainer.classList.add('hidden');
        textContainer.classList.add('hidden');
        articleContainer.classList.add('hidden');
        
        // Show the appropriate container based on selection
        if (type === 'text') {
            textContainer.classList.remove('hidden');
        } else if (type === 'article') {
            articleContainer.classList.remove('hidden');
        } else {
            urlContainer.classList.remove('hidden');
        }
        
        // Update button text
        const startButtonText = document.getElementById('startButtonText');
        startButtonText.textContent = 
            type === 'text' ? 'Analyze Text' :
            type === 'article' ? 'Analyze Article' :
            type === 'live' ? 'Start Live Transcription' :
            'Start Analysis';
    },

    async handleFormSubmit(e) {
        e.preventDefault();

        const videoType = elements.videoType.value;
        const language = elements.language.value;
        let input;

        switch (videoType) {
            case 'text':
                input = elements.textInput.value.trim();
                if (!Validators.isValidText(input)) {
                    UI.showError(Validators.getInputErrorMessage(videoType));
                    return;
                }
                break;
            case 'article':
                input = elements.articleUrl.value.trim();
                if (!Validators.isValidUrl(input)) {
                    UI.showError(Validators.getInputErrorMessage(videoType));
                    return;
                }
                break;
            default:
                input = elements.videoUrl.value.trim();
                if (!Validators.isValidUrl(input)) {
                    UI.showError(Validators.getInputErrorMessage(videoType));
                    return;
                }
        }

        UI.clearTranscription();
        UI.showLoading(true);

        switch (videoType) {
            case 'recorded':
                await TranscriptionManager.handleRecorded(input, language);
                break;
            case 'live':
                TranscriptionManager.startLive(input, language);
                break;
            case 'text':
                await TranscriptionManager.handleTextAnalysis(input, language);
                break;
            case 'article':
                await TranscriptionManager.handleArticleAnalysis(input, language);
                break;
        }
    },

    handleRefreshNews() {
        API.fetchTrendingNews()
            .then(news => UI.updateTrendingNews(news))
            .catch(error => UI.showError('Failed to refresh news'));
    }
};

// Initialize the application
const initializeApp = async () => {
    // Setup WebSocket
    WebSocketManager.setup();
    
    // Add event listeners
    elements.form.addEventListener('submit', EventHandlers.handleFormSubmit);
    elements.stopButton.addEventListener('click', TranscriptionManager.stopLive);
    elements.videoType.addEventListener('change', EventHandlers.handleVideoTypeChange);
    elements.refreshNews?.addEventListener('click', EventHandlers.handleRefreshNews);

    // Handle window resize for charts
    window.addEventListener('resize', () => {
        if (state.currentChartInstance) {
            Plotly.relayout(elements.confidenceChart, {
                width: elements.confidenceChart.offsetWidth
            });
        }
    });

    // Initialize news features
    try {
        const news = await API.fetchTrendingNews();
        UI.updateTrendingNews(news);
        API.setupNewsStream();
    } catch (error) {
        console.error('Error initializing news features:', error);
        UI.showError('Failed to load news content');
    }
};

// Start the application when DOM is loaded
document.addEventListener('DOMContentLoaded', initializeApp);

// Clean up function for when the page is unloaded
window.addEventListener('beforeunload', () => {
    if (state.ws) {
        state.ws.close();
    }
    if (state.newsEventSource) {
        state.newsEventSource.close();
    }
});
