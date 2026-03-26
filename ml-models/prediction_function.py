
# ============================================
# SAFEPASSAGE PREDICTION FUNCTION
# For Django Backend Integration
# ============================================

import joblib
import numpy as np
import pandas as pd

class SafePassagePredictor:
    def __init__(self):
        """Load the trained model and artifacts"""
        self.model = joblib.load('ml-models/safepassage_risk_model.pkl')
        self.scaler = joblib.load('ml-models/scaler.pkl')
        self.label_encoder = joblib.load('ml-models/label_encoder.pkl')
        self.feature_columns = joblib.load('ml-models/feature_columns.pkl')
        
    def predict_risk(self, input_data):
        """
        Predict risk category and score for given input
        
        Args:
            input_data: Dictionary with features
            Returns: Dictionary with prediction results
        """
        try:
            # Convert input to DataFrame
            if isinstance(input_data, dict):
                input_df = pd.DataFrame([input_data])
            else:
                input_df = input_data
            
            # Ensure all required features are present
            for col in self.feature_columns:
                if col not in input_df.columns:
                    input_df[col] = 0  # Default value
            
            # Select and order features
            input_features = input_df[self.feature_columns]
            
            # Scale features
            input_scaled = self.scaler.transform(input_features)
            
            # Predict
            risk_prediction = self.model.predict(input_scaled)[0]
            risk_probabilities = self.model.predict_proba(input_scaled)[0]
            
            # Convert prediction to label
            risk_category = self.label_encoder.inverse_transform([risk_prediction])[0]
            
            # Calculate risk score (0-100)
            risk_score = np.max(risk_probabilities) * 100
            
            # Get confidence for each category
            category_confidence = {
                self.label_encoder.inverse_transform([i])[0]: prob * 100 
                for i, prob in enumerate(risk_probabilities)
            }
            
            return {
                'risk_category': risk_category,
                'risk_score': round(risk_score, 2),
                'confidence_scores': category_confidence,
                'prediction_confidence': round(np.max(risk_probabilities) * 100, 2)
            }
            
        except Exception as e:
            return {
                'error': str(e),
                'risk_category': 'Unknown',
                'risk_score': 0,
                'confidence_scores': {},
                'prediction_confidence': 0
            }
    
    def batch_predict(self, input_data_list):
        """Predict for multiple inputs"""
        results = []
        for data in input_data_list:
            result = self.predict_risk(data)
            results.append(result)
        return results

# Usage Example:
# predictor = SafePassagePredictor()
# result = predictor.predict_risk({
#     'Total_Crimes': 1000,
#     'Avg_Severity': 5.5,
#     'Night_Crime_Ratio': 0.3,
#     'Weekend_Crime_Ratio': 0.25,
#     'Police_Deployment': 10,
#     'Case_Closure_Rate': 0.7,
#     'State_Crime_Rate': 200,
#     'Population_Density': 50,
#     'Tourist_Risk_Factor': 1.0,
#     'Night_Worker_Risk': 0.45
# })
# print(result)
