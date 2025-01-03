# TruthGuard: Real-Time Multi-Modal Misinformation Detection with Explainable AI and Audience Engagement

## Project Description
TruthGuard is a cutting-edge solution aimed at combating misinformation through real-time analysis and audience participation. This system leverages advanced AI models, multi-modal analysis, and blockchain verification to ensure transparent, accurate, and real-time detection of misinformation across various platforms and media formats.

## Key Functionalities
1. **Trending News Analysis (Real-Time Credibility Check):**
   - The platform monitors trending news in real-time and evaluates the credibility of the information.
   - By analyzing multiple sources, TruthGuard assigns credibility scores to news articles and provides visual indicators for users.

2. **Video Processing (Recorded and Live Streams):**
   - Supports two core functionalities for video analysis:
     - **Recorded Video Analysis:** Converts recorded video to mp3 using AssemblyAI and transcribes the mp3 to text using Rapido.
     - **Live Stream Analysis:** Extracts audio from live stream video in real-time, stores chunks in cache memory, and processes them to generate text without relying on Google APIs.

3. **Text Analysis and Prediction:**
   - Users can input any text or chunk of data for analysis. The system predicts the authenticity of the text (true or false) and visualizes the results through graphical representations.

4. **Article and URL Processing:**
   - Converts any article, research paper, or web page into text.
   - The extracted text is analyzed for misinformation detection, providing detailed predictions and credibility scores.

5. **Graph-Based Analysis and Visualization:**
   - The system visualizes the analysis results using interactive graphs, making it easier for users to understand patterns, trends, and the credibility of information.

## Technical Approach
- **Multi-modal Analysis:** Combines text and visual analysis using state-of-the-art models (BERT, GPT, CNNs, and Vision Transformers) to detect inconsistencies.
- **Explainable AI (XAI):** Tools like LIME and SHAP explain AI decisions with user-friendly visual explanations.
- **Real-time Platform:** A responsive web and mobile application (React/Flutter) supports real-time interaction and audience engagement.
- **Adaptive Learning:** The platform continuously improves by leveraging online and reinforcement learning.
- **Blockchain Verification:** Uses Ethereum/Hyperledger to ensure secure, transparent, and immutable fact-checking records.
- **Infrastructure:** Deployed on cloud platforms with Apache Kafka for live data ingestion, Spark Streaming for processing, and Neo4j for knowledge graphs.

## Target Audience
- Media Outlets and Broadcasters
- Journalists and Fact-Checkers
- General Public and Viewers
- Regulatory Bodies
- Research Organizations

## API and Library References
- **AssemblyAI API:** [Transcribe an Audio File](https://www.assemblyai.com/docs/getting-started/transcribe-an-audio-file)
- **RAPIDAPI Key for YouTube to MP3:** [YouTube to MP3 Download](https://rapidapi.com/CoolGuruji/api/youtube-to-mp3-download/playground/5902e92fe4b03f084dc0bb61)
