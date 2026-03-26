import joblib
import numpy as np
import os
import pandas as pd

# Define paths relative to this file
# This file is at safepassage_backend/safety/ml_model.py
# ml-models is at ml-models/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "../ml-models")

# Load models and artifacts
model = joblib.load(os.path.join(MODELS_DIR, 'safepassage_risk_model.pkl'))
scaler = joblib.load(os.path.join(MODELS_DIR, 'scaler.pkl'))
# The pipeline saved it as label_encoder.pkl
try:
    target_encoder = joblib.load(os.path.join(MODELS_DIR, 'target_encoder.pkl'))
except FileNotFoundError:
    target_encoder = joblib.load(os.path.join(MODELS_DIR, 'label_encoder.pkl'))

feature_columns = joblib.load(os.path.join(MODELS_DIR, 'feature_columns.pkl'))

def predict_risk(features_dict):
    """
    Predict risk based on input features.
    features_dict can be a list [year, crime_value] or a dictionary of features.
    """
    # Create a base feature set with all required columns initialized to 0
    input_data = {col: 0.0 for col in feature_columns}
    
    if isinstance(features_dict, list):
        # If it's the simplified list from the tutorial [year, crime_value]
        # We'll map them to reasonable defaults/available features
        # Assuming index 0 is year (we don't have year in training features but let's see)
        # Actually our training features are:
        # Total_Crimes, Avg_Severity, Night_Crime_Ratio, Weekend_Crime_Ratio, 
        # Police_Deployment, Case_Closure_Rate, State_Crime_Rate, Population_Density, 
        # Tourist_Risk_Factor, Night_Worker_Risk
        
        # We'll map features_dict[0] (year) -> maybe we don't use it or use it for something else
        # We'll map features_dict[1] (crime_value) -> Total_Crimes
        if len(features_dict) >= 2:
            input_data['Total_Crimes'] = features_dict[1]
            input_data['Avg_Severity'] = 5.0 # default medium
            input_data['Night_Crime_Ratio'] = 0.3
            input_data['Weekend_Crime_Ratio'] = 0.2
            input_data['Police_Deployment'] = 10
            input_data['Case_Closure_Rate'] = 0.6
            input_data['State_Crime_Rate'] = features_dict[1] / 10 # heuristic
            input_data['Population_Density'] = 100
            input_data['Tourist_Risk_Factor'] = 1.0
            input_data['Night_Worker_Risk'] = 0.4
    elif isinstance(features_dict, dict):
        for k, v in features_dict.items():
            if k in input_data:
                input_data[k] = v

    # Convert to DataFrame to ensure correct column order
    df = pd.DataFrame([input_data])[feature_columns]
    
    # Scale features
    features_scaled = scaler.transform(df)

    # Predict
    prediction = model.predict(features_scaled)
    probabilities = model.predict_proba(features_scaled)

    risk_label = target_encoder.inverse_transform(prediction)[0]
    risk_score = int(np.max(probabilities) * 100)

    return risk_label, risk_score
