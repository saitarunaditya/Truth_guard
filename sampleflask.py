from flask import Flask, request, jsonify
import joblib
import os
import numpy as np
import traceback

from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.naive_bayes import MultinomialNB

app = Flask(__name__)

# ================================
# Configuration
# ================================

# Directory where models are saved
OUTPUT_DIR = 'saved_models'

# Model file paths
VECTORIZER_PATH = os.path.join(OUTPUT_DIR, 'hashing_vectorizer.pkl')
ENSEMBLE_MODEL_PATH = os.path.join(OUTPUT_DIR, 'ensemble_model_live.pkl')

# ================================
# 1. Define the Ensemble Model Class (Same as in execution code)
# ================================

class EnsembleModel:
    def __init__(self, models):
        """
        Initialize the ensemble with a dictionary of models.
        """
        self.models = models  # Dictionary of models
    
    def predict_proba(self, X):
        """
        Predict probability estimates for each model and average them.
        """
        probas = []
        for model in self.models.values():
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)[:, 1]  # Probability of class 1
            else:
                raise AttributeError(f"Model {model} does not support predict_proba.")
            probas.append(proba)
        ensemble_proba = np.mean(probas, axis=0)
        return ensemble_proba
    
    def predict(self, X):
        """
        Predict class labels based on averaged probabilities.
        """
        ensemble_proba = self.predict_proba(X)
        return (ensemble_proba > 0.5).astype(int), ensemble_proba

# ================================
# 2. Load Models
# ================================

def load_vectorizer():
    if not os.path.exists(VECTORIZER_PATH):
        raise FileNotFoundError(f"Vectorizer file not found at '{VECTORIZER_PATH}'.")
    vectorizer = joblib.load(VECTORIZER_PATH)
    print("HashingVectorizer loaded successfully.")
    return vectorizer

def load_ensemble_model():
    if not os.path.exists(ENSEMBLE_MODEL_PATH):
        raise FileNotFoundError(f"Ensemble model file not found at '{ENSEMBLE_MODEL_PATH}'.")
    ensemble = joblib.load(ENSEMBLE_MODEL_PATH)
    print("Ensemble model loaded successfully.")
    return ensemble

try:
    vectorizer = load_vectorizer()
    ensemble = load_ensemble_model()
except Exception as e:
    print("Error loading models:", e)
    traceback.print_exc()
    exit(1)

# ================================
# 3. Define Prediction Function
# ================================

def predict_fake_news(title, text):
    """
    Predict whether the news is fake or true based on title and text.
    Returns the predicted label and confidence score.
    """
    content = f"{title} {text}"
    X = [content]
    X_transformed = vectorizer.transform(X)
    try:
        prediction, confidence = ensemble.predict(X_transformed)
        label = 'True' if prediction[0] == 1 else 'Fake'
        confidence_score = confidence[0]
        return label, confidence_score
    except Exception as e:
        print("Error during prediction:", e)
        traceback.print_exc()
        return None, None

# ================================
# 4. Define API Routes
# ================================

@app.route('/predict', methods=['POST'])
def predict():
    """
    Predict the label of a news article.
    Expects JSON payload with 'title' and 'text'.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No input data provided'}), 400
        
        title = data.get('title', '')
        text = data.get('text', '')
        
        if not title and not text:
            return jsonify({'error': 'Both title and text are missing'}), 400
        
        label, confidence = predict_fake_news(title, text)
        
        if label is None:
            return jsonify({'error': 'Prediction failed due to internal error'}), 500
        
        return jsonify({
            'label': label,
            'confidence': confidence
        }), 200
    
    except Exception as e:
        print("Error in /predict endpoint:", e)
        traceback.print_exc()
        return jsonify({'error': 'An error occurred during prediction'}), 500

@app.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint.
    """
    return jsonify({'status': 'OK'}), 200

# Optional: Endpoint to update the model (requires proper security in production)
@app.route('/update_model', methods=['POST'])
def update_model():
    """
    Update the ensemble model with new data.
    Expects JSON payload with 'title', 'text', and 'label'.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No input data provided'}), 400
        
        title = data.get('title', '')
        text = data.get('text', '')
        label = data.get('label', None)
        
        if not title and not text:
            return jsonify({'error': 'Both title and text are missing'}), 400
        if label not in [0, 1]:
            return jsonify({'error': 'Label must be 0 (Fake) or 1 (True)'}), 400
        
        content = f"{title} {text}"
        X_new = [content]
        y_new = [label]
        
        X_transformed = vectorizer.transform(X_new)
        
        # Update ensemble models
        for model in ensemble.models.values():
            if hasattr(model, 'partial_fit'):
                model.partial_fit(X_transformed, y_new, classes=np.array([0,1]))
            else:
                # For models that don't support partial_fit, retrain
                # This is a placeholder; retraining logic should be implemented as needed
                return jsonify({'error': f"Model {model} does not support partial_fit"}), 400
        
        # Optionally, save the updated ensemble model
        joblib.dump(ensemble, ENSEMBLE_MODEL_PATH)
        
        return jsonify({'message': 'Model updated successfully'}), 200
    
    except Exception as e:
        print("Error in /update_model endpoint:", e)
        traceback.print_exc()
        return jsonify({'error': 'An error occurred during model update'}), 500

# ================================
# 5. Run the Flask App
# ================================

if __name__ == '__main__':
    # For production, consider using a production WSGI server like Gunicorn
    app.run(host='0.0.0.0', port=5000, debug=False)
