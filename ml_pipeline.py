# ============================================
# SAFEPASSAGE AI/ML PIPELINE
# Urban Risk Prediction Model for Tourists & Night Workers
# ============================================

# -------------------------------
# 1. IMPORT REQUIRED LIBRARIES
# -------------------------------

import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import joblib
import warnings
from datetime import datetime
import json

from sklearn.model_selection import train_test_split, GridSearchCV, cross_val_score, StratifiedKFold
from sklearn.preprocessing import LabelEncoder, StandardScaler, MinMaxScaler
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    cohen_kappa_score
)
from sklearn.feature_selection import SelectKBest, f_classif

# Set style for better visualizations
plt.style.use('seaborn-v0_8')
sns.set_palette("husl")
warnings.filterwarnings('ignore')

# -------------------------------
# 2. DATASET LOADING AND INITIAL EXPLORATION
# -------------------------------

def load_and_explore_data():
    """Load and perform initial data exploration"""
    print("=" * 60)
    print("SAFEPASSAGE AI/ML PIPELINE - DATA LOADING")
    print("=" * 60)
    
    # Load both datasets
    dataset1_path = "dataset/Crime in India.csv"
    dataset2_path = "dataset/crime with names_dataset_india.csv"
    
    # Load state-wise crime data
    df_state = pd.read_csv(dataset1_path)
    print(f"State-wise dataset loaded: {df_state.shape}")
    
    # Load detailed crime records
    df_detailed = pd.read_csv(dataset2_path)
    print(f"Detailed crime dataset loaded: {df_detailed.shape}")
    
    print("\nState Dataset Columns:", df_state.columns.tolist())
    print("\nDetailed Dataset Columns:", df_detailed.columns.tolist())
    
    return df_state, df_detailed

# -------------------------------
# 3. DATA PREPROCESSING AND FEATURE ENGINEERING
# -------------------------------

def preprocess_data(df_state, df_detailed):
    """Comprehensive data preprocessing and feature engineering"""
    print("\n" + "=" * 60)
    print("DATA PREPROCESSING AND FEATURE ENGINEERING")
    print("=" * 60)
    
    # Process state-wise data
    df_state_clean = df_state.copy()
    
    # Remove total rows and clean data
    df_state_clean = df_state_clean[~df_state_clean['State/UT'].str.contains('Total', na=False)]
    df_state_clean = df_state_clean.dropna()
    
    # Create comprehensive features from state data
    df_state_clean['Crime_Growth_Rate'] = (
        (df_state_clean['2022'] - df_state_clean['2020']) / df_state_clean['2020'] * 100
    ).fillna(0)
    
    df_state_clean['Crime_Density'] = (
        df_state_clean['2022'] / df_state_clean['Mid-Year Projected Population (in Lakhs) (2022)']
    ).fillna(0)
    
    # Risk categorization based on multiple factors
    df_state_clean['Risk_Score'] = (
        df_state_clean['Rate of Cognizable Crimes (IPC) (2022)'] * 0.4 +
        df_state_clean['Crime_Density'] * 0.3 +
        df_state_clean['Crime_Growth_Rate'] * 0.3
    )
    
    # Process detailed crime data
    df_detailed_clean = df_detailed.copy()
    df_detailed_clean = df_detailed_clean.dropna()
    
    # Convert datetime columns with flexible parsing
    df_detailed_clean['Date Reported'] = pd.to_datetime(df_detailed_clean['Date Reported'], dayfirst=True, errors='coerce')
    df_detailed_clean['Date of Occurrence'] = pd.to_datetime(df_detailed_clean['Date of Occurrence'], dayfirst=True, errors='coerce')
    df_detailed_clean['Time of Occurrence'] = pd.to_datetime(df_detailed_clean['Time of Occurrence'], dayfirst=True, errors='coerce')
    
    # Extract temporal features
    df_detailed_clean['Hour'] = df_detailed_clean['Time of Occurrence'].dt.hour
    df_detailed_clean['Day_of_Week'] = df_detailed_clean['Date of Occurrence'].dt.dayofweek
    df_detailed_clean['Month'] = df_detailed_clean['Date of Occurrence'].dt.month
    
    # Create night risk feature (10 PM - 6 AM)
    df_detailed_clean['Is_Night_Time'] = ((df_detailed_clean['Hour'] >= 22) | 
                                          (df_detailed_clean['Hour'] <= 6)).astype(int)
    
    # Weekend risk feature
    df_detailed_clean['Is_Weekend'] = (df_detailed_clean['Day_of_Week'] >= 5).astype(int)
    
    # Crime severity mapping
    crime_severity = {
        'HOMICIDE': 10, 'SEXUAL ASSAULT': 9, 'KIDNAPPING': 8, 'ASSAULT': 7,
        'ARSON': 6, 'EXTORTION': 6, 'ROBBERY': 7, 'BURGLARY': 5,
        'VEHICLE - STOLEN': 4, 'FRAUD': 3, 'CYBERCRIME': 3, 'VANDALISM': 2,
        'PUBLIC INTOXICATION': 1, 'COUNTERFEITING': 2, 'DRUG OFFENSE': 3,
        'IDENTITY THEFT': 3
    }
    
    df_detailed_clean['Crime_Severity'] = df_detailed_clean['Crime Description'].map(crime_severity).fillna(3)
    
    # Create city-wise risk aggregation
    city_risk = df_detailed_clean.groupby('City').agg({
        'Crime Code': 'count',
        'Crime_Severity': 'mean',
        'Is_Night_Time': 'sum',
        'Police Deployed': 'mean',
        'Case Closed': lambda x: (x == 'Yes').mean()
    }).reset_index()
    
    city_risk.columns = ['City', 'Total_Crimes', 'Avg_Severity', 'Night_Crimes', 
                        'Avg_Police_Deployed', 'Case_Closure_Rate']
    
    # Calculate city risk score
    city_risk['City_Risk_Score'] = (
        city_risk['Total_Crimes'] * 0.3 +
        city_risk['Avg_Severity'] * 0.25 +
        city_risk['Night_Crimes'] * 0.2 +
        (1 - city_risk['Case_Closure_Rate']) * 0.15 +
        (1 - city_risk['Avg_Police_Deployed']/20) * 0.1
    )
    
    return df_state_clean, df_detailed_clean, city_risk

# -------------------------------
# 4. EXPLORATORY DATA ANALYSIS (EDA)
# -------------------------------

def perform_eda(df_state, df_detailed, city_risk):
    """Comprehensive exploratory data analysis with visualizations"""
    print("\n" + "=" * 60)
    print("EXPLORATORY DATA ANALYSIS")
    print("=" * 60)
    
    # Create output directory for plots
    os.makedirs("ml-models/plots", exist_ok=True)
    
    # 1. State-wise Crime Distribution
    plt.figure(figsize=(15, 8))
    top_states = df_state.nlargest(15, '2022')
    plt.barh(top_states['State/UT'], top_states['2022'], color='crimson', alpha=0.7)
    plt.title('Top 15 States by Crime Count (2022)', fontsize=16, fontweight='bold')
    plt.xlabel('Crime Count')
    plt.ylabel('State/UT')
    plt.tight_layout()
    plt.savefig('ml-models/plots/state_crime_distribution.png', dpi=300, bbox_inches='tight')
    plt.show()
    
    # 2. Crime Rate vs Population Density
    plt.figure(figsize=(12, 8))
    scatter = plt.scatter(df_state['Mid-Year Projected Population (in Lakhs) (2022)'], 
                         df_state['Rate of Cognizable Crimes (IPC) (2022)'],
                         c=df_state['Risk_Score'], cmap='Reds', alpha=0.6, s=100)
    plt.colorbar(scatter, label='Risk Score')
    plt.title('Crime Rate vs Population Density', fontsize=16, fontweight='bold')
    plt.xlabel('Population (in Lakhs)')
    plt.ylabel('Crime Rate per 100,000')
    plt.xscale('log')
    plt.tight_layout()
    plt.savefig('ml-models/plots/crime_vs_population.png', dpi=300, bbox_inches='tight')
    plt.show()
    
    # 3. Time-based Crime Analysis
    plt.figure(figsize=(15, 10))
    
    # Hourly distribution
    plt.subplot(2, 2, 1)
    hourly_crime = df_detailed['Hour'].value_counts().sort_index()
    plt.plot(hourly_crime.index, hourly_crime.values, marker='o', linewidth=2, color='blue')
    plt.axvspan(22, 24, alpha=0.2, color='red', label='Night Time')
    plt.axvspan(0, 6, alpha=0.2, color='red')
    plt.title('Hourly Crime Distribution')
    plt.xlabel('Hour of Day')
    plt.ylabel('Number of Crimes')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    # Day of week distribution
    plt.subplot(2, 2, 2)
    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    daily_crime = df_detailed['Day_of_Week'].value_counts().sort_index()
    plt.bar(day_names, daily_crime.values, color='green', alpha=0.7)
    plt.title('Crime Distribution by Day of Week')
    plt.ylabel('Number of Crimes')
    plt.grid(True, alpha=0.3)
    
    # Monthly distribution
    plt.subplot(2, 2, 3)
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    monthly_crime = df_detailed['Month'].value_counts().sort_index()
    plt.plot(month_names, monthly_crime.values, marker='s', linewidth=2, color='orange')
    plt.title('Monthly Crime Distribution')
    plt.ylabel('Number of Crimes')
    plt.xticks(rotation=45)
    plt.grid(True, alpha=0.3)
    
    # Night vs Day Crime
    plt.subplot(2, 2, 4)
    night_data = df_detailed['Is_Night_Time'].value_counts()
    labels = ['Day Time', 'Night Time']
    colors = ['lightblue', 'darkblue']
    plt.pie(night_data.values, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90)
    plt.title('Night vs Day Crime Distribution')
    
    plt.tight_layout()
    plt.savefig('ml-models/plots/temporal_analysis.png', dpi=300, bbox_inches='tight')
    plt.show()
    
    # 4. Crime Severity Analysis
    plt.figure(figsize=(12, 6))
    severity_counts = df_detailed['Crime Description'].value_counts().head(10)
    plt.barh(severity_counts.index, severity_counts.values, color='purple', alpha=0.7)
    plt.title('Top 10 Crime Types', fontsize=16, fontweight='bold')
    plt.xlabel('Count')
    plt.tight_layout()
    plt.savefig('ml-models/plots/crime_types.png', dpi=300, bbox_inches='tight')
    plt.show()
    
    # 5. City Risk Analysis
    plt.figure(figsize=(14, 8))
    top_cities = city_risk.nlargest(15, 'City_Risk_Score')
    plt.barh(top_cities['City'], top_cities['City_Risk_Score'], color='red', alpha=0.7)
    plt.title('Top 15 Cities by Risk Score', fontsize=16, fontweight='bold')
    plt.xlabel('Risk Score')
    plt.ylabel('City')
    plt.tight_layout()
    plt.savefig('ml-models/plots/city_risk_scores.png', dpi=300, bbox_inches='tight')
    plt.show()
    
    # 6. Correlation Heatmap
    plt.figure(figsize=(12, 10))
    
    # Prepare correlation data
    corr_data = city_risk[['Total_Crimes', 'Avg_Severity', 'Night_Crimes', 
                           'Avg_Police_Deployed', 'Case_Closure_Rate', 'City_Risk_Score']]
    correlation_matrix = corr_data.corr()
    
    sns.heatmap(correlation_matrix, annot=True, cmap='coolwarm', center=0, 
                square=True, fmt='.2f', cbar_kws={"shrink": .8})
    plt.title('Feature Correlation Matrix', fontsize=16, fontweight='bold')
    plt.tight_layout()
    plt.savefig('ml-models/plots/correlation_heatmap.png', dpi=300, bbox_inches='tight')
    plt.show()

# -------------------------------
# 5. FEATURE ENGINEERING FOR MODELING
# -------------------------------

def prepare_modeling_data(df_state, df_detailed, city_risk):
    """Prepare final dataset for machine learning"""
    print("\n" + "=" * 60)
    print("FEATURE ENGINEERING FOR MODELING")
    print("=" * 60)
    
    # Create comprehensive modeling dataset
    modeling_data = []
    
    # Process each city with detailed features
    for _, city_row in city_risk.iterrows():
        city_name = city_row['City']
        
        # Get city-specific detailed data
        city_detailed = df_detailed[df_detailed['City'] == city_name]
        
        if len(city_detailed) > 0:
            # Calculate city-specific temporal risk factors
            night_risk_ratio = city_detailed['Is_Night_Time'].mean()
            weekend_risk_ratio = city_detailed['Is_Weekend'].mean()
            avg_severity = city_detailed['Crime_Severity'].mean()
            
            # Find corresponding state data
            state_match = df_state[df_state['State/UT'].str.contains(city_name.split()[0], case=False, na=False)]
            
            if len(state_match) > 0:
                state_data = state_match.iloc[0]
                state_crime_rate = state_data['Rate of Cognizable Crimes (IPC) (2022)']
                state_population = state_data['Mid-Year Projected Population (in Lakhs) (2022)']
            else:
                state_crime_rate = df_state['Rate of Cognizable Crimes (IPC) (2022)'].median()
                state_population = df_state['Mid-Year Projected Population (in Lakhs) (2022)'].median()
            
            # Create feature vector
            features = {
                'City': city_name,
                'Total_Crimes': city_row['Total_Crimes'],
                'Avg_Severity': avg_severity,
                'Night_Crime_Ratio': night_risk_ratio,
                'Weekend_Crime_Ratio': weekend_risk_ratio,
                'Police_Deployment': city_row['Avg_Police_Deployed'],
                'Case_Closure_Rate': city_row['Case_Closure_Rate'],
                'State_Crime_Rate': state_crime_rate,
                'Population_Density': state_population,
                'Tourist_Risk_Factor': np.random.uniform(0.8, 1.2),  # Simulated tourist attractiveness
                'Night_Worker_Risk': night_risk_ratio * 1.5,  # Enhanced night risk for workers
            }
            
            modeling_data.append(features)
    
    # Convert to DataFrame
    model_df = pd.DataFrame(modeling_data)
    
    # Create risk categories based on comprehensive scoring
    risk_scores = []
    for _, row in model_df.iterrows():
        # Comprehensive risk calculation
        base_risk = (
            row['Total_Crimes'] * 0.2 +
            row['Avg_Severity'] * 0.15 +
            row['Night_Crime_Ratio'] * 0.2 +
            row['State_Crime_Rate'] * 0.15 +
            (1 - row['Case_Closure_Rate']) * 0.1 +
            row['Night_Worker_Risk'] * 0.1 +
            row['Tourist_Risk_Factor'] * 0.1
        )
        risk_scores.append(base_risk)
    
    model_df['Calculated_Risk_Score'] = risk_scores
    
    # Create risk categories (Low, Medium, High)
    risk_thresholds = [0, model_df['Calculated_Risk_Score'].quantile(0.33), 
                      model_df['Calculated_Risk_Score'].quantile(0.67), float('inf')]
    risk_labels = ['Low', 'Medium', 'High']
    
    model_df['Risk_Category'] = pd.cut(
        model_df['Calculated_Risk_Score'],
        bins=risk_thresholds,
        labels=risk_labels,
        include_lowest=True
    )
    
    print(f"Final modeling dataset shape: {model_df.shape}")
    print(f"Risk distribution:\n{model_df['Risk_Category'].value_counts()}")
    
    return model_df

# -------------------------------
# 6. MODEL TRAINING AND EVALUATION
# -------------------------------

def train_and_evaluate_models(model_df):
    """Train multiple models and select the best one"""
    print("\n" + "=" * 60)
    print("MODEL TRAINING AND EVALUATION")
    print("=" * 60)
    
    # Prepare features and target
    feature_columns = [col for col in model_df.columns if col not in ['City', 'Risk_Category', 'Calculated_Risk_Score']]
    X = model_df[feature_columns]
    y = model_df['Risk_Category']
    
    # Encode target variable
    label_encoder = LabelEncoder()
    y_encoded = label_encoder.fit_transform(y)
    
    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_encoded, test_size=0.2, random_state=42, stratify=y_encoded
    )
    
    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    # Define models to test
    models = {
        'Random Forest': RandomForestClassifier(random_state=42),
        'Gradient Boosting': GradientBoostingClassifier(random_state=42),
        'Logistic Regression': LogisticRegression(random_state=42, max_iter=1000),
        'SVM': SVC(random_state=42, probability=True)
    }
    
    # Hyperparameter grids
    param_grids = {
        'Random Forest': {
            'n_estimators': [100, 200],
            'max_depth': [10, 20, None],
            'min_samples_split': [2, 5]
        },
        'Gradient Boosting': {
            'n_estimators': [100, 200],
            'learning_rate': [0.01, 0.1],
            'max_depth': [3, 5]
        },
        'Logistic Regression': {
            'C': [0.1, 1, 10],
            'penalty': ['l2']
        },
        'SVM': {
            'C': [1, 10],
            'kernel': ['rbf', 'linear']
        }
    }
    
    best_models = {}
    model_performance = {}
    
    # Train and evaluate each model
    for model_name, model in models.items():
        print(f"\nTraining {model_name}...")
        
        # Grid search with cross-validation
        grid_search = GridSearchCV(
            model, param_grids[model_name], cv=5, scoring='accuracy', n_jobs=-1
        )
        grid_search.fit(X_train_scaled, y_train)
        
        # Best model
        best_model = grid_search.best_estimator_
        best_models[model_name] = best_model
        
        # Predictions
        y_pred = best_model.predict(X_test_scaled)
        y_pred_proba = best_model.predict_proba(X_test_scaled) if hasattr(best_model, 'predict_proba') else None
        
        # Metrics
        accuracy = accuracy_score(y_test, y_pred)
        precision = precision_score(y_test, y_pred, average='weighted')
        recall = recall_score(y_test, y_pred, average='weighted')
        f1 = f1_score(y_test, y_pred, average='weighted')
        
        # Cross-validation score
        cv_scores = cross_val_score(best_model, X_train_scaled, y_train, cv=5)
        cv_mean = cv_scores.mean()
        cv_std = cv_scores.std()
        
        model_performance[model_name] = {
            'accuracy': accuracy,
            'precision': precision,
            'recall': recall,
            'f1_score': f1,
            'cv_mean': cv_mean,
            'cv_std': cv_std,
            'best_params': grid_search.best_params_
        }
        
        print(f"Best Parameters: {grid_search.best_params_}")
        print(f"Accuracy: {accuracy:.4f} (+/- {cv_std * 2:.4f})")
        print(f"Precision: {precision:.4f}")
        print(f"Recall: {recall:.4f}")
        print(f"F1-Score: {f1:.4f}")
    
    # Select best model based on accuracy and stability
    best_model_name = max(model_performance.keys(), 
                         key=lambda x: (model_performance[x]['accuracy'], 
                                       -model_performance[x]['cv_std']))
    
    print(f"\nBest Model: {best_model_name}")
    print(f"Performance: {model_performance[best_model_name]}")
    
    return best_models[best_model_name], scaler, label_encoder, feature_columns, model_performance

# -------------------------------
# 7. DETAILED MODEL EVALUATION
# -------------------------------

def detailed_evaluation(model, X_test, y_test, label_encoder, model_name):
    """Perform detailed evaluation of the best model"""
    print("\n" + "=" * 60)
    print(f"DETAILED EVALUATION - {model_name}")
    print("=" * 60)
    
    # Predictions
    y_pred = model.predict(X_test)
    y_pred_proba = model.predict_proba(X_test) if hasattr(model, 'predict_proba') else None
    
    # Detailed metrics
    accuracy = accuracy_score(y_test, y_pred)
    precision = precision_score(y_test, y_pred, average='weighted')
    recall = recall_score(y_test, y_pred, average='weighted')
    f1 = f1_score(y_test, y_pred, average='weighted')
    
    # Kappa score
    kappa = cohen_kappa_score(y_test, y_pred)
    
    print(f"Accuracy: {accuracy:.4f}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall: {recall:.4f}")
    print(f"F1-Score: {f1:.4f}")
    print(f"Cohen's Kappa: {kappa:.4f}")
    
    # Classification Report
    class_names = label_encoder.classes_
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=class_names))
    
    # Confusion Matrix Visualization
    cm = confusion_matrix(y_test, y_pred)
    plt.figure(figsize=(8, 6))
    sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', 
                xticklabels=class_names, yticklabels=class_names)
    plt.title(f'Confusion Matrix - {model_name}', fontsize=16, fontweight='bold')
    plt.xlabel('Predicted')
    plt.ylabel('Actual')
    plt.tight_layout()
    plt.savefig(f'ml-models/plots/confusion_matrix_{model_name.replace(" ", "_").lower()}.png', 
                dpi=300, bbox_inches='tight')
    plt.show()
    
    # Feature Importance (if available)
    if hasattr(model, 'feature_importances_'):
        feature_importance = pd.DataFrame({
            'feature': [f'Feature_{i}' for i in range(len(model.feature_importances_))],
            'importance': model.feature_importances_
        }).sort_values('importance', ascending=False)
        
        plt.figure(figsize=(10, 8))
        top_features = feature_importance.head(10)
        plt.barh(top_features['feature'], top_features['importance'], color='green', alpha=0.7)
        plt.title('Top 10 Feature Importances', fontsize=16, fontweight='bold')
        plt.xlabel('Importance')
        plt.tight_layout()
        plt.savefig('ml-models/plots/feature_importance.png', dpi=300, bbox_inches='tight')
        plt.show()
        
        print("\nTop 10 Important Features:")
        print(feature_importance.head(10))
    
    return accuracy, precision, recall, f1, kappa

# -------------------------------
# 8. SAVE MODEL AND ARTIFACTS
# -------------------------------

def save_model_and_artifacts(model, scaler, label_encoder, feature_columns, model_performance):
    """Save the trained model and all necessary artifacts"""
    print("\n" + "=" * 60)
    print("SAVING MODEL AND ARTIFACTS")
    print("=" * 60)
    
    # Create models directory
    os.makedirs("ml-models", exist_ok=True)
    
    # Save model
    model_path = "ml-models/safepassage_risk_model.pkl"
    joblib.dump(model, model_path)
    print(f"Model saved: {model_path}")
    
    # Save scaler
    scaler_path = "ml-models/scaler.pkl"
    joblib.dump(scaler, scaler_path)
    print(f"Scaler saved: {scaler_path}")
    
    # Save label encoder
    encoder_path = "ml-models/label_encoder.pkl"
    joblib.dump(label_encoder, encoder_path)
    print(f"Label encoder saved: {encoder_path}")
    
    # Save feature columns
    features_path = "ml-models/feature_columns.pkl"
    joblib.dump(feature_columns, features_path)
    print(f"Feature columns saved: {features_path}")
    
    # Save model performance metrics
    performance_path = "ml-models/model_performance.json"
    with open(performance_path, 'w') as f:
        json.dump(model_performance, f, indent=4)
    print(f"Performance metrics saved: {performance_path}")
    
    # Save model metadata
    metadata = {
        'model_type': 'Risk Classification',
        'risk_categories': label_encoder.classes_.tolist(),
        'feature_columns': feature_columns,
        'model_accuracy': model_performance['Random Forest']['accuracy'],  # Assuming RF is best
        'created_date': datetime.now().isoformat(),
        'version': '1.0'
    }
    
    metadata_path = "ml-models/model_metadata.json"
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=4)
    print(f"Model metadata saved: {metadata_path}")

# -------------------------------
# 9. PREDICTION FUNCTION FOR BACKEND INTEGRATION
# -------------------------------

def create_prediction_function():
    """Create a prediction function for Django backend integration"""
    prediction_code = '''
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
'''
    
    with open("ml-models/prediction_function.py", "w") as f:
        f.write(prediction_code)
    
    print("Prediction function saved: ml-models/prediction_function.py")

# -------------------------------
# 10. MAIN EXECUTION
# -------------------------------

def main():
    """Main execution function"""
    print("SAFEPASSAGE AI/ML PIPELINE STARTING...")
    print("Urban Risk Prediction Model for Tourists & Night Workers")
    
    try:
        # Step 1: Load and explore data
        df_state, df_detailed = load_and_explore_data()
        
        # Step 2: Preprocess and engineer features
        df_state_clean, df_detailed_clean, city_risk = preprocess_data(df_state, df_detailed)
        
        # Step 3: Perform EDA
        perform_eda(df_state_clean, df_detailed_clean, city_risk)
        
        # Step 4: Prepare modeling data
        model_df = prepare_modeling_data(df_state_clean, df_detailed_clean, city_risk)
        
        # Step 5: Train and evaluate models
        best_model, scaler, label_encoder, feature_columns, model_performance = train_and_evaluate_models(model_df)
        
        # Step 6: Detailed evaluation
        # Split data again for detailed evaluation
        X = model_df[feature_columns]
        y = label_encoder.transform(model_df['Risk_Category'])
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        X_test_scaled = scaler.transform(X_test)
        
        accuracy, precision, recall, f1, kappa = detailed_evaluation(
            best_model, X_test_scaled, y_test, label_encoder, "Random Forest"
        )
        
        # Step 7: Save model and artifacts
        save_model_and_artifacts(best_model, scaler, label_encoder, feature_columns, model_performance)
        
        # Step 8: Create prediction function
        create_prediction_function()
        
        # Final Summary
        print("\n" + "=" * 60)
        print("SAFEPASSAGE AI/ML PIPELINE COMPLETED SUCCESSFULLY!")
        print("=" * 60)
        print(f"Final Model Accuracy: {accuracy:.4f}")
        print(f"Precision: {precision:.4f}")
        print(f"Recall: {recall:.4f}")
        print(f"F1-Score: {f1:.4f}")
        print(f"Cohen's Kappa: {kappa:.4f}")
        print("\nFiles Generated:")
        print("- ml-models/safepassage_risk_model.pkl (Trained Model)")
        print("- ml-models/scaler.pkl (Feature Scaler)")
        print("- ml-models/label_encoder.pkl (Label Encoder)")
        print("- ml-models/feature_columns.pkl (Feature Names)")
        print("- ml-models/model_performance.json (Performance Metrics)")
        print("- ml-models/model_metadata.json (Model Information)")
        print("- ml-models/prediction_function.py (Backend Integration)")
        print("- ml-models/plots/ (Visualization Plots)")
        
        print("\nModel is ready for Django backend integration!")
        
    except Exception as e:
        print(f"Error in pipeline execution: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
