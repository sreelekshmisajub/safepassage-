from ..models import CrimeRecord
from django.utils import timezone
from datetime import timedelta

def calculate_route_risk(worker_location=None):
    """
    Real-time risk calculation logic based on recent crime activity.
    Logic:
    - Count crimes in the last 24 hours (expanded for better demonstration)
    - Return a risk percentage score
    """
    recent_time = timezone.now() - timedelta(hours=24)

    crimes = CrimeRecord.objects.filter(
        time__gte=recent_time
    )

    crime_count = crimes.count()

    # Kerala-specific risk calculation (lower base risk for safer areas)
    if crime_count < 3:
        return 15  # Very safe - typical for Kerala
    elif crime_count < 8:
        return 35  # Moderate risk
    else:
        return 65  # Higher risk but still manageable

def get_weather_risk(city):
    """
    Example Weather Risk logic (mocked for stability, but structure for API integration)
    In production: Replace with real requests.get(OpeanWeatherAPI)
    """
    import random
    # Simulated weather severity for Indian monsoon/seasonal patterns
    weather_in = random.choice(["Thunderstorm", "Rain", "Clear", "Mist"])
    
    if weather_in in ["Thunderstorm", "Rain"]:
        return 15
    return 0
