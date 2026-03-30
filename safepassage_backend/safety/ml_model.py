import joblib
import numpy as np
import pandas as pd
from pathlib import Path

# Resolve the repo root before building artifact paths so Windows \\?\ paths
# do not keep raw ".." segments, which breaks joblib open() during manage.py commands.
REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "ml-models"


def _load_model_artifact(filename):
    return joblib.load(MODELS_DIR / filename)

# Load models and artifacts
model = _load_model_artifact('safepassage_risk_model.pkl')
scaler = _load_model_artifact('scaler.pkl')
# The pipeline saved it as label_encoder.pkl
try:
    target_encoder = _load_model_artifact('target_encoder.pkl')
except FileNotFoundError:
    target_encoder = _load_model_artifact('label_encoder.pkl')

feature_columns = _load_model_artifact('feature_columns.pkl')

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
