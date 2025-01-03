from flask import Flask, request, jsonify
import joblib
import os
import numpy as np

# Initialize Flask app
app = Flask(__name__)

# Define the EnsembleModel class
class EnsembleModel:
    def __init__(self, models):
        self.models = models
    
    def fit(self, X, y):
        for model in self.models.values():
            model.fit(X, y)
    
    def predict_proba(self, X):
        predictions = np.zeros((X.shape[0], 2))
        for model in self.models.values():
            predictions += model.predict_proba(X)
        return predictions / len(self.models)
    
    def predict(self, X):
        proba = self.predict_proba(X)
        return (proba[:, 1] > 0.5).astype(int)

# File paths
MODEL_PATH = r'saved_models\ensemble_model.pkl'
PIPELINE_PATH = r'saved_models\pipeline.pkl'

# Load the ensemble model and preprocessing pipeline
if not os.path.exists(MODEL_PATH) or not os.path.exists(PIPELINE_PATH):
    raise FileNotFoundError("Model or pipeline not found. Please train and save them first.")

ensemble_model = joblib.load(MODEL_PATH)
pipeline = joblib.load(PIPELINE_PATH)

# Define route for prediction
@app.route('/predict', methods=['POST'])
def predict():
    try:
        # Get JSON data from request
        data = request.get_json()
        text = data.get('text', '')

        if not text:
            return jsonify({"error": "No text provided."}), 400

        # Preprocess the input text using the pipeline
        processed_text = pipeline.transform([text])

        # Make a prediction using the ensemble model
        prediction = ensemble_model.predict(processed_text)
        confidence = ensemble_model.predict_proba(processed_text)[:, 1]

        # Map prediction to True/False
        prediction_label = "True" if prediction[0] == 1 else "False"

        # Return the prediction as JSON
        return jsonify({
            'prediction': prediction_label,
            'confidence': float(confidence[0])
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'models_loaded': os.path.exists(MODEL_PATH) and os.path.exists(PIPELINE_PATH)
    })
if __name__ == '__main__':
    app.run(debug=True)
