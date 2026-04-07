from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .ml_model import predict_risk
import csv
import html
import json
import math
import os
import re
import socket
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from django.http import JsonResponse
from smtplib import SMTPException

from django.core.mail import EmailMessage, get_connection, send_mail
from django.conf import settings
from .models import SafePassageUser, UserLocation, RiskZone, EmergencyAlert, IncidentReport, CulturalGuide, TouristProfile, JourneyDetail, EmergencyContact, Shift, SafeHaven, CheckIn, WorkerProfile, CrimeRecord, RiskPrediction
from .services.risk_engine import calculate_route_risk
from django.utils import translation
from django.views.decorators.csrf import csrf_exempt # Optional, but recommended to use with csrf token in fetch
from functools import wraps, lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

def worker_required(view_func):
    @wraps(view_func)
    @login_required(login_url='login')
    def wrapper(request, *args, **kwargs):
        if request.user.role == 'worker' or request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        return render(request, 'unauthorized.html')
    return wrapper


def tourist_required(view_func):
    @wraps(view_func)
    @login_required(login_url='login')
    def wrapper(request, *args, **kwargs):
        if request.user.role == 'tourist' or request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        return render(request, 'unauthorized.html')
    return wrapper


def admin_required(view_func):
    @wraps(view_func)
    @login_required(login_url='login')
    def wrapper(request, *args, **kwargs):
        if request.user.role == 'admin' or request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        return render(request, 'unauthorized.html')
    return wrapper


SMTP_PLACEHOLDER_USERS = {
    "admin@safepassage-india.org",
    "yourgmail@gmail.com",
    "admin@example.com",
}
SMTP_PLACEHOLDER_PASSWORDS = {
    "",
    "your_app_password",
    "change_me",
    "changeme",
}


def _notification_sender_email():
    return (
        getattr(settings, "DEFAULT_FROM_EMAIL", "")
        or getattr(settings, "EMAIL_HOST_USER", "")
        or "alerts@safepassage.local"
    )


def _notification_delivery_status():
    backend = getattr(settings, "EMAIL_BACKEND", "") or ""
    sender = _notification_sender_email()
    uses_smtp = backend == "django.core.mail.backends.smtp.EmailBackend"
    issues = []

    if not backend:
        issues.append("EMAIL_BACKEND is not set.")

    if uses_smtp:
        if not getattr(settings, "EMAIL_HOST", ""):
            issues.append("EMAIL_HOST is missing.")
        if not getattr(settings, "EMAIL_PORT", None):
            issues.append("EMAIL_PORT is missing.")
        if not getattr(settings, "EMAIL_HOST_USER", ""):
            issues.append("EMAIL_HOST_USER is missing.")
        elif getattr(settings, "EMAIL_HOST_USER", "") in SMTP_PLACEHOLDER_USERS:
            issues.append("EMAIL_HOST_USER still uses the sample placeholder value.")
        if getattr(settings, "EMAIL_HOST_PASSWORD", "") in SMTP_PLACEHOLDER_PASSWORDS:
            issues.append("EMAIL_HOST_PASSWORD is missing or still uses the sample placeholder value.")

    if not sender:
        issues.append("DEFAULT_FROM_EMAIL or EMAIL_HOST_USER must be configured.")

    security = "SSL" if getattr(settings, "EMAIL_USE_SSL", False) else "TLS" if getattr(settings, "EMAIL_USE_TLS", False) else "None"
    configured = not issues

    return {
        "configured": configured,
        "uses_smtp": uses_smtp,
        "backend": backend or "Not configured",
        "backend_label": (backend.rsplit(".", 1)[-1] if backend else "Not configured"),
        "host": getattr(settings, "EMAIL_HOST", "") or "Not set",
        "port": getattr(settings, "EMAIL_PORT", "") or "Not set",
        "security": security,
        "sender": sender or "Not configured",
        "status_label": "Configured" if configured else "Needs setup",
        "status_slug": "success" if configured else "warning",
        "issues": issues,
    }


def _send_broadcast_notifications(subject, message_body, sender, recipient_list):
    connection = get_connection(fail_silently=False)
    delivered = 0
    failed_recipients = []

    try:
        connection.open()
        for recipient in recipient_list:
            email = EmailMessage(
                subject=subject,
                body=message_body,
                from_email=sender,
                to=[recipient],
                connection=connection,
            )
            try:
                delivered += email.send(fail_silently=False)
            except Exception:
                failed_recipients.append(recipient)
    finally:
        try:
            connection.close()
        except Exception:
            pass

    return delivered, failed_recipients


EMBASSY_DIRECTORY = {
    "us": {
        "country": "United States",
        "embassy_name": "U.S. Embassy New Delhi",
        "city": "New Delhi",
        "phone": "+91-11-2419-8000",
        "emergency_number": "+91-11-2419-8000",
        "address": "Shantipath, Chanakyapuri, New Delhi 110021",
    },
    "uk": {
        "country": "United Kingdom",
        "embassy_name": "British High Commission New Delhi",
        "city": "New Delhi",
        "phone": "+91-11-2419-2100",
        "emergency_number": "+91-11-2419-2100",
        "address": "Shantipath, Chanakyapuri, New Delhi 110021",
    },
    "canada": {
        "country": "Canada",
        "embassy_name": "High Commission of Canada in India",
        "city": "New Delhi",
        "phone": "+91-11-4178-2000",
        "emergency_number": "+91-11-4178-2000",
        "address": "7/8 Shantipath, Chanakyapuri, New Delhi 110021",
    },
    "australia": {
        "country": "Australia",
        "embassy_name": "Australian High Commission New Delhi",
        "city": "New Delhi",
        "phone": "+91-11-4139-9900",
        "emergency_number": "+91-11-4139-9900",
        "address": "1/50 G Shantipath, Chanakyapuri, New Delhi 110021",
    },
}

OFFICIAL_EMERGENCY_LINES = [
    {
        "label": "Police",
        "phone": "112",
        "description": "National emergency response",
    },
    {
        "label": "Ambulance",
        "phone": "108",
        "description": "National ambulance support",
    },
    {
        "label": "Women Helpline",
        "phone": "181",
        "description": "Emergency support line",
    },
]

EMERGENCY_PHRASEBOOK = {
    "help me i am in danger": {
        "hi": "मदद कीजिए, मैं खतरे में हूँ।",
        "ml": "ദയവായി സഹായിക്കൂ, ഞാൻ അപകടത്തിലാണ്.",
        "ta": "தயவு செய்து உதவுங்கள், நான் ஆபத்தில் இருக்கிறேன்.",
        "te": "దయచేసి సహాయం చేయండి, నేను ప్రమాదంలో ఉన్నాను.",
        "kn": "ದಯವಿಟ್ಟು ಸಹಾಯ ಮಾಡಿ, ನಾನು ಅಪಾಯದಲ್ಲಿದ್ದೇನೆ.",
    },
    "call the police": {
        "hi": "कृपया पुलिस को बुलाइए।",
        "ml": "ദയവായി പോലീസിനെ വിളിക്കൂ.",
        "ta": "தயவு செய்து போலீஸை அழைக்கவும்.",
        "te": "దయచేసి పోలీసులను పిలవండి.",
        "kn": "ದಯವಿಟ್ಟು ಪೊಲೀಸರನ್ನು ಕರೆಸಿ.",
    },
    "i need an ambulance": {
        "hi": "मुझे एम्बुलेंस चाहिए।",
        "ml": "എനിക്ക് ആംബുലൻസ് വേണം.",
        "ta": "எனக்கு ஆம்புலன்ஸ் வேண்டும்.",
        "te": "నాకు అంబులెన్స్ కావాలి.",
        "kn": "ನನಗೆ ಆಂಬುಲೆನ್ಸ್ ಬೇಕು.",
    },
    "where is the nearest hospital": {
        "hi": "नजदीकी अस्पताल कहाँ है?",
        "ml": "ഏറ്റവും അടുത്തുള്ള ആശുപത്രി എവിടെയാണ്?",
        "ta": "அருகிலுள்ள மருத்துவமனை எங்கே உள்ளது?",
        "te": "దగ్గరలోని ఆసుಪత్రి ఎక్కడ ఉంది?",
        "kn": "ಹತ್ತಿರದ ಆಸ್ಪತ್ರೆ ಎಲ್ಲಿದೆ?",
    },
    "i am lost": {
        "hi": "मैं रास्ता भटक गया हूँ।",
        "ml": "ഞാൻ വഴി തെറ്റിപ്പോയി.",
        "ta": "நான் வழி தவறிவிட்டேன்.",
        "te": "నేను దారి తప్పాను.",
        "kn": "ನಾನು ದಾರಿ ತಪ್ಪಿದ್ದೇನೆ.",
    },
    "i need water": {
        "hi": "मुझे पानी चाहिए।",
        "ml": "എനിക്ക് വെള്ളം വേണം.",
        "ta": "எனக்குத் தண்ணீர் வேண்டும்.",
        "te": "నాకు నీళ్లు కావాలి.",
        "kn": "ನನಗೆ ನೀರು ಬೇಕು.",
    },
    "i need medicine": {
        "hi": "मुझे दवा चाहिए।",
        "ml": "എനിക്ക് മരുന്ന് വേണം.",
        "ta": "எனக்கு மருந்து வேண்டும்.",
        "te": "నాకు మందులు కావాలి.",
        "kn": "ನನಗೆ ಔಷಧಿ ಬೇಕು.",
    },
    "there is a fire": {
        "hi": "यहाँ आग लगी है!",
        "ml": "ഇവിടെ തീ പിടിച്ചിട്ടുണ്ട്!",
        "ta": "இங்கே தீ பிடித்துள்ளது!",
        "te": "ಇక్కడ నిప్పు రాజుకుంది!",
        "kn": "ಇಲ್ಲಿ ಬೆಂಕಿ ಹತ್ತಿಕೊಂಡಿದೆ!",
    },
    "stop": {
        "hi": "रुकिए!",
        "ml": "നിൽക്കൂ!",
        "ta": "நில்லுங்கள்!",
        "te": "ఆగండి!",
        "kn": "ನಿಲ್ಲಿಸಿ!",
    },
}

EMERGENCY_PHRASE_MATCHERS = {
    "help me i am in danger": [
        r"\bhelp\b.*\bdanger\b",
        r"\bi am in danger\b",
        r"\bim in danger\b",
        r"\bplease help me\b",
        r"\bhelp me\b",
        r"\bhelp\b",
        r"\bneed help\b",
        r"\bunsafe\b",
        r"\bnot safe\b",
    ],
    "call the police": [
        r"\bcall\b.*\bpolice\b",
        r"\bpolice\b",
        r"\bpolice station\b",
        r"\bneed police\b",
    ],
    "i need an ambulance": [
        r"\bambulance\b",
        r"\bmedical help\b",
        r"\bneed medical\b",
        r"\bneed a doctor\b",
        r"\bdoctor\b",
    ],
    "where is the nearest hospital": [
        r"\bhospital\b",
        r"\bnearest hospital\b",
        r"\bwhere is hospital\b",
    ],
    "i am lost": [
        r"\blost\b",
        r"\bam lost\b",
        r"\bi am lost\b",
        r"\bi'm lost\b",
        r"\bdont know where i am\b",
    ],
    "i need water": [
        r"\bwater\b",
        r"\bthirsty\b",
        r"\bneed water\b",
    ],
    "i need medicine": [
        r"\bmedicine\b",
        r"\bmeds\b",
        r"\bpharmacy\b",
        r"\bchemist\b",
    ],
    "there is a fire": [
        r"\bfire\b",
        r"\bburning\b",
        r"\bhelp fire\b",
    ],
    "stop": [
        r"\bstop\b",
        r"\bstop it\b",
        r"\bdon't do that\b",
    ],
}


def _normalize_language_code(raw_value, default="en"):
    value = (raw_value or "").strip().lower()
    if not value:
        return default
    return value.split("-")[0].split("_")[0] or default

def _role_api_guard(request, allowed_roles, message):
    if request.user.role not in allowed_roles and not request.user.is_superuser:
        return JsonResponse({"status": "error", "message": message}, status=403)
    return None


def _tourist_api_guard(request):
    return _role_api_guard(request, {"tourist"}, "Tourist access required.")


def _worker_api_guard(request):
    return _role_api_guard(request, {"worker"}, "Night worker access required.")


def _travel_mode_api_guard(request):
    return _role_api_guard(request, {"tourist", "worker"}, "Tourist or night worker access required.")


def _admin_api_guard(request):
    return _role_api_guard(request, {"admin"}, "Admin access required.")


def _load_request_payload(request):
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return {}
    return request.POST


def _parse_coordinates(source):
    try:
        lat = float(source.get("lat", source.get("latitude")))
        lng = float(source.get("lng", source.get("longitude")))
        return lat, lng
    except (TypeError, ValueError):
        return None, None


def _haversine_km(lat1, lng1, lat2, lng2):
    earth_radius_km = 6371
    lat1_rad, lng1_rad = math.radians(lat1), math.radians(lng1)
    lat2_rad, lng2_rad = math.radians(lat2), math.radians(lng2)
    delta_lat = lat2_rad - lat1_rad
    delta_lng = lng2_rad - lng1_rad
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng / 2) ** 2
    )
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def _nearby_records(queryset, lat, lng, lat_field="latitude", lng_field="longitude", radius_km=5):
    nearby = []
    for record in queryset:
        distance_km = _haversine_km(lat, lng, getattr(record, lat_field), getattr(record, lng_field))
        if distance_km <= radius_km:
            nearby.append((record, round(distance_km, 2)))
    nearby.sort(key=lambda item: item[1])
    return nearby


def _remote_service_enabled():
    return not any(arg == "test" for arg in sys.argv)


def _reverse_geocode_name(lat, lng):
    if not _remote_service_enabled():
        return None

    endpoint = "https://nominatim.openstreetmap.org/reverse"
    query_string = urlencode(
        {
            "lat": f"{lat:.6f}",
            "lon": f"{lng:.6f}",
            "format": "jsonv2",
            "zoom": 14,
            "addressdetails": 1,
        }
    )
    request = Request(
        f"{endpoint}?{query_string}",
        headers={
            "User-Agent": "SafePassage/1.0 (tourist-reverse-geocode)",
            "Accept": "application/json",
            "Accept-Language": "en",
        },
    )

    try:
        with urlopen(request, timeout=2.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return None

    address = payload.get("address") or {}
    parts = [
        address.get("suburb") or address.get("neighbourhood") or address.get("road"),
        address.get("city") or address.get("town") or address.get("village") or address.get("county"),
        address.get("state"),
    ]
    cleaned_parts = []
    for part in parts:
        if not part:
            continue
        if cleaned_parts and cleaned_parts[-1].strip().lower() == part.strip().lower():
            continue
        cleaned_parts.append(part.strip())
    if cleaned_parts:
        return ", ".join(cleaned_parts[:2])

    display_name = (payload.get("display_name") or "").strip()
    return ", ".join(segment.strip() for segment in display_name.split(",")[:2] if segment.strip()) or None


def _resolve_location_name(lat, lng):
    zones = list(RiskZone.objects.exclude(city="")[:100])
    closest_name = None
    closest_distance = None

    for zone in zones:
        distance_km = _haversine_km(lat, lng, zone.latitude, zone.longitude)
        if closest_distance is None or distance_km < closest_distance:
            closest_distance = distance_km
            closest_name = zone.city

    if closest_name and closest_distance is not None and closest_distance <= 20:
        return closest_name

    return _reverse_geocode_name(lat, lng)


def _compose_location_label(lat, lng, include_coordinates=False, precision=4):
    if lat is None or lng is None:
        return None
    coordinate_label = f"{lat:.{precision}f}, {lng:.{precision}f}"
    resolved_name = _resolve_location_name(lat, lng)
    if include_coordinates:
        return f"{resolved_name} ({coordinate_label})" if resolved_name else coordinate_label
    return resolved_name or coordinate_label


def _weather_code_context(weather_code, wind_speed):
    if weather_code in {95, 96, 99}:
        return 85, "Thunderstorm", "Severe weather detected. Delay travel if possible and stay near active shelters."
    if weather_code in {65, 67, 75, 77, 82, 86}:
        return 72, "Severe Rain/Snow", "Heavy precipitation can reduce visibility and route safety. Avoid isolated roads."
    if weather_code in {45, 48, 51, 53, 55, 56, 57, 61, 63, 71, 73, 80, 81, 85}:
        return 48, "Caution", "Reduced visibility or surface conditions may affect travel. Prefer well-lit main roads."
    if wind_speed is not None and wind_speed >= 35:
        return 45, "High Wind", "Strong winds may affect outdoor movement. Use well-protected routes."
    if weather_code in {1, 2, 3}:
        return 18, "Cloudy", "Cloud cover is present, but conditions are still generally manageable."
    return 8, "Clear", "Current weather conditions are favorable."


def _weather_payload(lat, lng):
    if not _remote_service_enabled():
        return {
            "available": False,
            "source": None,
            "risk_score": None,
            "risk_label": "UNAVAILABLE",
            "condition": None,
            "temperature_c": None,
            "advice": "Live weather is unavailable during automated test runs.",
        }

    endpoint = "https://api.open-meteo.com/v1/forecast"
    query_string = urlencode(
        {
            "latitude": f"{lat:.6f}",
            "longitude": f"{lng:.6f}",
            "current_weather": "true",
            "timezone": "auto",
        }
    )
    request = Request(
        f"{endpoint}?{query_string}",
        headers={
            "User-Agent": "SafePassage/1.0 (tourist-weather)",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=2.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return {
            "available": False,
            "source": None,
            "risk_score": None,
            "risk_label": "UNAVAILABLE",
            "condition": None,
            "temperature_c": None,
            "advice": "Live weather provider is currently unavailable for this location.",
        }

    current_weather = payload.get("current_weather") or {}
    try:
        temperature_c = round(float(current_weather.get("temperature")), 1)
    except (TypeError, ValueError):
        temperature_c = None
    try:
        wind_speed = round(float(current_weather.get("windspeed")), 1)
    except (TypeError, ValueError):
        wind_speed = None
    try:
        weather_code = int(current_weather.get("weathercode"))
    except (TypeError, ValueError):
        weather_code = None

    if weather_code is None:
        return {
            "available": False,
            "source": "open-meteo",
            "risk_score": None,
            "risk_label": "UNAVAILABLE",
            "condition": None,
            "temperature_c": temperature_c,
            "advice": "Weather data was returned without a usable condition code.",
        }

    risk_score, condition, advice = _weather_code_context(weather_code, wind_speed)
    return {
        "available": True,
        "source": "open-meteo",
        "risk_score": risk_score,
        "risk_label": _normalize_risk_label(risk_score),
        "condition": condition,
        "temperature_c": temperature_c,
        "wind_speed_kph": wind_speed,
        "advice": advice,
    }


def _normalize_risk_label(score):
    if score >= 75:
        return "HIGH"
    if score >= 45:
        return "MEDIUM"
    return "LOW"


def _optional_risk_label(score):
    if score is None:
        return "UNAVAILABLE"
    return _normalize_risk_label(score)


def _risk_advice(score):
    if score >= 75:
        return "Avoid isolated stretches after dark and move toward hospitals, police stations, or busy public zones."
    if score >= 45:
        return "Stay alert, prefer main roads, and keep transport and embassy contacts ready."
    return "Area is relatively stable. Continue standard travel precautions and keep belongings secured."


def _ml_risk_estimate(zone_score, crime_count, crowd_factor, weather_score, scam_signal):
    try:
        feature_payload = {
            "Total_Crimes": max(zone_score, 10) + (crime_count * 6),
            "Avg_Severity": max(1.0, zone_score / 20),
            "Night_Crime_Ratio": min(1.0, 0.25 + (weather_score / 100)),
            "Weekend_Crime_Ratio": min(1.0, 0.15 + (crowd_factor / 100)),
            "Police_Deployment": max(5.0, 18 - (zone_score / 10)),
            "Case_Closure_Rate": max(0.2, 0.75 - (crowd_factor / 100)),
            "State_Crime_Rate": zone_score / 10,
            "Population_Density": 80 + (crowd_factor * 3),
            "Tourist_Risk_Factor": 1.0 + (scam_signal / 50),
            "Night_Worker_Risk": 0.25,
        }
        model_label, confidence = predict_risk(feature_payload)
        return model_label, int(confidence)
    except Exception:
        return None, None


def _build_risk_payload(lat, lng):
    nearby_zones = _nearby_records(RiskZone.objects.all(), lat, lng, radius_km=6)
    nearby_crimes = _nearby_records(
        CrimeRecord.objects.filter(time__gte=timezone.now() - timedelta(days=7)),
        lat,
        lng,
        radius_km=6,
    )
    nearby_reports = _nearby_records(
        IncidentReport.objects.filter(created_at__gte=timezone.now() - timedelta(days=7)),
        lat,
        lng,
        radius_km=6,
    )
    nearby_resources = _nearby_records(SafeHaven.objects.all(), lat, lng, radius_km=8)
    weather = _weather_payload(lat, lng)
    zone_signal = (
        int(sum(zone.risk_score for zone, _ in nearby_zones) / len(nearby_zones))
        if nearby_zones
        else None
    )
    scam_zones = [zone for zone, _ in nearby_zones if zone.risk_type == "scam"]
    scam_signal = (
        int(sum(zone.risk_score for zone in scam_zones) / len(scam_zones))
        if scam_zones
        else None
    )
    crime_count = len(nearby_crimes)
    report_count = len(nearby_reports)
    crime_signal = min(100, crime_count * 18) if crime_count else None
    crowd_signal = min(100, report_count * 22) if report_count else None
    weather_signal = weather["risk_score"] if weather.get("available") and weather.get("risk_score") is not None else None

    weighted_components = []
    if zone_signal is not None:
        weighted_components.append((zone_signal, 0.55))
    if crime_signal is not None:
        weighted_components.append((crime_signal, 0.20))
    if crowd_signal is not None:
        weighted_components.append((crowd_signal, 0.15))
    if scam_signal is not None:
        weighted_components.append((scam_signal, 0.10))
    if weather_signal is not None:
        weighted_components.append((weather_signal, 0.10))

    if weighted_components:
        total_weight = sum(weight for _, weight in weighted_components)
        risk_score = int(round(sum(value * weight for value, weight in weighted_components) / total_weight))
        risk_label = _normalize_risk_label(risk_score)
        advice = _risk_advice(risk_score)
        data_available = True
    else:
        risk_score = None
        risk_label = "UNAVAILABLE"
        advice = "No live safety records are stored near this location yet."
        data_available = False

    location_name = _resolve_location_name(lat, lng)

    hotspots = [
        {
            "risk_type": zone.get_risk_type_display(),
            "risk_score": zone.risk_score,
            "description": zone.description,
            "city": zone.city,
            "latitude": zone.latitude,
            "longitude": zone.longitude,
            "distance_km": distance_km,
        }
        for zone, distance_km in nearby_zones[:3]
    ]

    resources = [
        {
            "name": haven.name,
            "type": haven.get_type_display(),
            "latitude": haven.latitude,
            "longitude": haven.longitude,
            "address": haven.address,
            "phone": haven.phone,
            "distance_km": distance_km,
            "is_open_24_7": haven.is_open_24_7,
        }
        for haven, distance_km in nearby_resources[:4]
    ]

    return {
        "location": location_name,
        "coordinates": {
            "latitude": round(lat, 6),
            "longitude": round(lng, 6),
        },
        "data_available": data_available,
        "risk_label": risk_label,
        "risk_score": risk_score,
        "advice": advice,
        "breakdown": {
            "zone_signal": zone_signal,
            "crime_signal": crime_signal,
            "crowd_signal": crowd_signal,
            "scam_signal": scam_signal,
            "weather_signal": weather_signal,
        },
        "weather": weather,
        "model_label": None,
        "model_confidence": None,
        "signal_counts": {
            "risk_zones": len(nearby_zones),
            "crime_records": crime_count,
            "incident_reports": report_count,
            "safe_resources": len(nearby_resources),
        },
        "nearby_hotspots": hotspots,
        "nearby_resources": resources,
    }


def _resolve_user_coordinates(user):
    latest_location = UserLocation.objects.filter(user=user).order_by("-timestamp").first()
    if latest_location:
        return latest_location.latitude, latest_location.longitude
    return None, None


def _normalize_lookup_text(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower()).strip()


def _build_cultural_context_terms(location_name, alerts, risk_payload, dataset_context):
    raw_terms = []
    if location_name:
        raw_terms.append(location_name)
    if dataset_context and dataset_context.get("city"):
        raw_terms.append(dataset_context["city"])
        raw_terms.extend(dataset_context.get("top_crimes", [])[:3])
        raw_terms.extend(dataset_context.get("top_domains", [])[:3])

    for alert in alerts[:6]:
        raw_terms.extend(
            [
                alert.get("title") or "",
                alert.get("description") or "",
                alert.get("incident_type") or "",
                alert.get("location_label") or "",
            ]
        )

    for hotspot in risk_payload.get("nearby_hotspots", [])[:5]:
        raw_terms.extend(
            [
                hotspot.get("city") or "",
                hotspot.get("description") or "",
                hotspot.get("risk_type") or "",
            ]
        )

    for resource in risk_payload.get("nearby_resources", [])[:4]:
        raw_terms.extend(
            [
                resource.get("name") or "",
                resource.get("type") or "",
                resource.get("address") or "",
            ]
        )

    explicit_short_terms = {"bus", "cab", "atm", "taxi", "rail", "metro"}
    terms = []
    seen = set()
    for term in raw_terms:
        normalized = _normalize_lookup_text(term)
        if not normalized:
            continue
        if normalized not in seen:
            seen.add(normalized)
            terms.append(normalized)
        for token in normalized.split():
            if len(token) >= 4 or token in explicit_short_terms:
                if token not in seen:
                    seen.add(token)
                    terms.append(token)
    return terms


def _cultural_entry_match_score(entry, context_terms):
    if not context_terms:
        return 0

    haystack = _normalize_lookup_text(
        " ".join(
            [
                (entry.title or "").strip(),
                (entry.content or "").strip(),
            ]
        )
    )
    if not haystack:
        return 0

    score = 0
    for term in context_terms:
        if not term:
            continue
        if haystack == term:
            score += 6
        elif haystack.startswith(term):
            score += 5
        elif f" {term}" in haystack or term in haystack:
            score += 3
    return score


def _collect_cultural_entries(language, category, limit=4, context_terms=None):
    normalized_language = _normalize_language_code(language, default="en")
    queryset = list(
        CulturalGuide.objects.filter(language=normalized_language, category=category)
    )
    if not queryset and normalized_language != "en":
        queryset = list(CulturalGuide.objects.filter(language="en", category=category))

    scored_entries = []
    for index, item in enumerate(queryset):
        match_score = _cultural_entry_match_score(item, context_terms or [])
        scored_entries.append((match_score, -index, item))

    if context_terms and any(score > 0 for score, _, _ in scored_entries):
        scored_entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected_entries = [item for score, _, item in scored_entries[:limit]]
        selected_scores = {item.id: score for score, _, item in scored_entries[:limit]}
    else:
        selected_entries = queryset[:limit]
        selected_scores = {item.id: 0 for item in selected_entries}

    return [
        {
            "title": item.title.strip() or category.title(),
            "content": item.content.strip(),
            "language": item.language,
            "source": "cultural-guide",
            "source_label": "Location-matched guide" if selected_scores.get(item.id, 0) > 0 else "Guide library",
            "match_score": selected_scores.get(item.id, 0),
        }
        for item in selected_entries
        if item.content.strip()
    ]


@lru_cache(maxsize=1)
def _load_city_crime_context():
    dataset_path = Path(settings.BASE_DIR).parent / "dataset" / "crime with names_dataset_india.csv"
    if not dataset_path.exists():
        return {}

    summaries = {}
    with dataset_path.open("r", encoding="utf-8-sig", newline="") as dataset_handle:
        reader = csv.DictReader(dataset_handle)
        for row in reader:
            city = (row.get("City") or "").strip()
            crime_description = (row.get("Crime Description") or "").strip()
            crime_domain = (row.get("Crime Domain") or "").strip()
            if not city:
                continue

            city_key = _normalize_lookup_text(city)
            entry = summaries.setdefault(
                city_key,
                {
                    "city": city,
                    "report_count": 0,
                    "crime_counter": Counter(),
                    "domain_counter": Counter(),
                },
            )
            entry["report_count"] += 1
            if crime_description:
                entry["crime_counter"][crime_description.title()] += 1
            if crime_domain:
                entry["domain_counter"][crime_domain] += 1

    for key, entry in summaries.items():
        entry["top_crimes"] = [label for label, _ in entry["crime_counter"].most_common(3)]
        entry["top_domains"] = [label for label, _ in entry["domain_counter"].most_common(3)]
        entry.pop("crime_counter", None)
        entry.pop("domain_counter", None)
    return summaries


def _city_dataset_context(location_name):
    normalized_location = _normalize_lookup_text(location_name)
    if not normalized_location:
        return None

    candidates = []
    location_tokens = set(normalized_location.split())
    for city_key, entry in _load_city_crime_context().items():
        city_tokens = set(city_key.split())
        overlap = len(location_tokens & city_tokens)
        if not overlap:
            continue
        score = overlap
        if city_key in normalized_location:
            score += 5
        if normalized_location in city_key:
            score += 2
        candidates.append((score, len(city_key), entry))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], item[2]["report_count"]), reverse=True)
    matched_entry = candidates[0][2]
    return {
        "available": True,
        "city": matched_entry["city"],
        "report_count": matched_entry["report_count"],
        "top_crimes": matched_entry["top_crimes"],
        "top_domains": matched_entry["top_domains"],
    }


def _build_incident_alerts(lat, lng, limit=5):
    recent_reports = _nearby_records(
        IncidentReport.objects.filter(created_at__gte=timezone.now() - timedelta(days=7)),
        lat,
        lng,
        radius_km=8,
    )
    nearby_zones = _nearby_records(RiskZone.objects.all(), lat, lng, radius_km=8)

    alerts = [
        {
            "id": report.id,
            "incident_type": report.incident_type,
            "title": report.location_label or f"{report.get_incident_type_display()} reported nearby",
            "description": report.description,
            "severity": _optional_risk_label(report.risk_score_snapshot),
            "distance_km": distance_km,
            "latitude": report.latitude,
            "longitude": report.longitude,
            "source": "user-report",
            "created_at": report.created_at.isoformat(),
        }
        for report, distance_km in recent_reports[:limit]
    ]

    if len(alerts) < limit:
        for zone, distance_km in nearby_zones[: limit - len(alerts)]:
            alerts.append(
                {
                    "id": f"zone-{zone.id}",
                    "incident_type": zone.risk_type,
                    "title": f"{zone.get_risk_type_display()} hotspot",
                    "description": zone.description,
                    "severity": _normalize_risk_label(zone.risk_score),
                    "distance_km": distance_km,
                    "latitude": zone.latitude,
                    "longitude": zone.longitude,
                    "source": "risk-zone",
                    "created_at": timezone.now().isoformat(),
                }
            )

    return alerts


def _build_cultural_safety_payload(user, lat, lng, language, assist_language=None):
    normalized_language = _normalize_language_code(language, default="en")
    assist_language = _normalize_language_code(assist_language, default="hi")
    location_name = _resolve_location_name(lat, lng)
    risk_payload = _build_risk_payload(lat, lng)
    embassy_payload = _default_embassy_payload(type("obj", (), {"user": user})(), lat, lng)
    alerts = _build_incident_alerts(lat, lng, limit=6)
    nearby_resources = risk_payload.get("nearby_resources", [])
    dataset_context = _city_dataset_context(location_name) or {
        "available": False,
        "city": None,
        "report_count": 0,
        "top_crimes": [],
        "top_domains": [],
    }
    context_terms = _build_cultural_context_terms(location_name, alerts, risk_payload, dataset_context)

    dos = _collect_cultural_entries(normalized_language, "do", context_terms=context_terms)
    donts = _collect_cultural_entries(normalized_language, "dont", context_terms=context_terms)
    risk_behaviors = _collect_cultural_entries(normalized_language, "scam", context_terms=context_terms)

    restricted_keywords = (
        "restricted",
        "sensitive",
        "government",
        "temple",
        "mosque",
        "church",
        "religious",
        "photography",
        "permit",
        "security",
        "protest",
        "demonstration",
        "curfew",
        "night",
        "after",
    )
    restricted_zones = []
    for hotspot in risk_payload.get("nearby_hotspots", []):
        description_text = (hotspot.get("description") or "").strip()
        haystack = _normalize_lookup_text(f"{hotspot.get('city', '')} {description_text}")
        if hotspot.get("risk_score", 0) < 70 and not any(keyword in haystack for keyword in restricted_keywords):
            continue
        restricted_zones.append(
            {
                "name": hotspot.get("city") or hotspot.get("risk_type") or "Sensitive area",
                "description": description_text or "High-risk zone recorded near this location.",
                "risk_score": hotspot.get("risk_score"),
                "risk_label": _optional_risk_label(hotspot.get("risk_score")),
                "distance_km": hotspot.get("distance_km"),
                "source": "risk-zone",
            }
        )

    scam_keywords = ("scam", "overcharge", "fake", "guide", "theft", "fraud", "pickpocket", "taxi")
    scam_alerts = []
    for alert in alerts:
        haystack = _normalize_lookup_text(
            f"{alert.get('incident_type', '')} {alert.get('title', '')} {alert.get('description', '')}"
        )
        if any(keyword in haystack for keyword in scam_keywords):
            scam_alerts.append(alert)

    quick_help = []
    for source_text in ("Help me", "Call police", "Where is embassy?"):
        translated_text, translation_mode = _translate_phrase(source_text, assist_language)
        quick_help.append(
            {
                "source_text": source_text,
                "translated_text": translated_text,
                "translation_mode": translation_mode,
            }
        )

    risk_explanation = []
    signal_counts = risk_payload.get("signal_counts", {})
    if signal_counts.get("risk_zones"):
        risk_explanation.append(f"{signal_counts['risk_zones']} nearby risk-zone records influence the cultural score.")
    if signal_counts.get("incident_reports"):
        risk_explanation.append(f"{signal_counts['incident_reports']} recent crowd-sourced incident reports were found nearby.")
    if scam_alerts:
        risk_explanation.append("Tourist scam or fraud-related alerts are recorded around this area.")
    if restricted_zones:
        risk_explanation.append("Sensitive or restricted zones are recorded close to your current position.")
    if dataset_context.get("available"):
        top_crime_text = ", ".join(dataset_context.get("top_crimes", [])[:2])
        if top_crime_text:
            risk_explanation.append(
                f"City crime dataset match for {dataset_context['city']}: common records include {top_crime_text}."
            )
    weather = risk_payload.get("weather", {})
    if weather.get("available") and weather.get("risk_label") not in {"LOW", "UNAVAILABLE"}:
        risk_explanation.append(f"Weather conditions currently add {weather.get('risk_label', '').lower()} travel risk.")

    emergency_resources = [
        resource
        for resource in nearby_resources
        if resource.get("type") in {"Police Station", "Hospital"}
    ][:4]

    def _tip_text(entry):
        return " ".join(
            part
            for part in [
                (entry.get("title") or "").strip(),
                (entry.get("content") or "").strip(),
            ]
            if part
        ).strip()

    def _filter_entries(entries, keywords):
        filtered = []
        for entry in entries:
            haystack = _normalize_lookup_text(_tip_text(entry))
            if any(keyword in haystack for keyword in keywords):
                filtered.append(
                    {
                        "title": entry.get("title") or "Live guidance",
                        "content": entry.get("content") or "",
                        "source": "cultural-guide",
                        "source_label": entry.get("source_label") or "Guide library",
                    }
                )
        return filtered

    all_guidance_entries = [*dos, *donts]
    local_customs = _filter_entries(
        all_guidance_entries,
        ("temple", "mosque", "church", "respect", "custom", "etiquette", "greet", "festival", "queue"),
    )
    dress_codes = _filter_entries(
        all_guidance_entries,
        ("dress", "modest", "scarf", "cover", "sleeve", "shoulder", "footwear", "shoe", "attire", "clothing"),
    )
    behavior_guidelines = _filter_entries(
        all_guidance_entries,
        ("public", "transport", "noise", "loud", "argument", "photography", "photo", "touch", "gesture", "behavior", "queue"),
    )
    restricted_actions = _filter_entries(
        donts,
        ("avoid", "dont", "do not", "restricted", "permit", "prohibited", "after", "night", "photography", "government", "security", "curfew"),
    )
    for zone in restricted_zones[:4]:
        restricted_actions.append(
            {
                "title": zone.get("name") or "Sensitive zone",
                "content": zone.get("description") or "Restricted activity has been recorded near this location.",
                "source": zone.get("source") or "risk-zone",
                "source_label": "Live zone signal",
            }
        )

    cultural_risk_score_meta = {
        "purpose": "Helps tourists avoid location-specific behavior risks before they turn into scams, conflict, or unsafe movement decisions.",
        "calculation_factors": [
            "Nearby risk-zone and incident density around your live location",
            "Active scam or fraud-related alerts near the tourist corridor",
            "Sensitive or restricted zones close to the current area",
            "Matched city crime dataset patterns for the location",
            "Weather risk when it affects safe public movement",
        ],
    }

    return {
        "status": "success",
        "country": "India",
        "location": location_name,
        "coordinates": {
            "latitude": round(lat, 6),
            "longitude": round(lng, 6),
        },
        "language": normalized_language,
        "assist_language": assist_language,
        "risk_score": risk_payload.get("risk_score"),
        "risk_label": risk_payload.get("risk_label"),
        "risk_advice": risk_payload.get("advice"),
        "risk_explanation": risk_explanation,
        "cultural_tips": {
            "dos": dos,
            "donts": donts,
            "risk_behaviors": risk_behaviors,
        },
        "dos": dos,
        "donts": donts,
        "risk_behaviors": risk_behaviors,
        "scam_alerts": scam_alerts[:5],
        "restricted_zones": restricted_zones[:4],
        "quick_help": quick_help,
        "location_insights": {
            "local_customs": local_customs[:4],
            "dress_codes": dress_codes[:4],
            "behavior_guidelines": behavior_guidelines[:4],
            "restricted_actions": restricted_actions[:6],
        },
        "cultural_risk_score_meta": cultural_risk_score_meta,
        "live_context": {
            "location_name": location_name,
            "matched_context_terms": context_terms[:8],
            "guide_matches": {
                "dos": sum(1 for item in dos if item.get("match_score", 0) > 0),
                "donts": sum(1 for item in donts if item.get("match_score", 0) > 0),
                "scams": sum(1 for item in risk_behaviors if item.get("match_score", 0) > 0),
            },
        },
        "emergency": {
            "embassy": embassy_payload,
            "official_lines": OFFICIAL_EMERGENCY_LINES,
            "nearby_resources": emergency_resources,
            "emergency_contacts_count": EmergencyContact.objects.filter(user=user).count(),
        },
        "real_time_alerts": alerts[:5],
        "dataset_context": dataset_context,
        "data_available": any(
            [
                dos,
                donts,
                risk_behaviors,
                scam_alerts,
                restricted_zones,
                alerts,
                dataset_context.get("available"),
            ]
        ),
    }


def _build_route_destination_catalog():
    destinations = []

    city_entries = {}
    for zone in RiskZone.objects.exclude(city="").order_by("city", "-risk_score"):
        city_name = (zone.city or "").strip()
        if not city_name:
            continue

        city_key = city_name.lower()
        if city_key not in city_entries:
            city_entries[city_key] = {
                "id": f"area-{len(city_entries) + 1}",
                "name": city_name,
                "kind": "Area Intelligence",
                "description": zone.description or "",
                "latitude_total": zone.latitude,
                "longitude_total": zone.longitude,
                "count": 1,
                "risk_score": zone.risk_score,
            }
            continue

        city_entries[city_key]["latitude_total"] += zone.latitude
        city_entries[city_key]["longitude_total"] += zone.longitude
        city_entries[city_key]["count"] += 1
        if zone.risk_score > city_entries[city_key]["risk_score"]:
            city_entries[city_key]["risk_score"] = zone.risk_score
            city_entries[city_key]["description"] = zone.description or city_entries[city_key]["description"]

    for entry in city_entries.values():
        destinations.append(
            {
                "id": entry["id"],
                "name": entry["name"],
                "kind": entry["kind"],
                "description": entry["description"],
                "latitude": round(entry["latitude_total"] / entry["count"], 6),
                "longitude": round(entry["longitude_total"] / entry["count"], 6),
            }
        )

    seen_safe_havens = set()
    for haven in SafeHaven.objects.exclude(name="").order_by("name"):
        name = (haven.name or "").strip()
        key = (name.lower(), round(haven.latitude, 6), round(haven.longitude, 6))
        if not name or key in seen_safe_havens:
            continue

        seen_safe_havens.add(key)
        destinations.append(
            {
                "id": f"safe-haven-{haven.id}",
                "name": name,
                "kind": haven.get_type_display(),
                "description": haven.address or "",
                "latitude": round(haven.latitude, 6),
                "longitude": round(haven.longitude, 6),
            }
        )

    destinations.sort(key=lambda item: (item["name"].lower(), item["kind"].lower()))
    return destinations[:80]


def _local_india_place_matches(query, limit=10):
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return []

    scored_matches = []
    seen = set()

    for destination in _build_route_destination_catalog():
        destination_name = destination.get("name", "").strip()
        destination_kind = destination.get("kind", "").strip()
        destination_description = destination.get("description", "").strip()
        haystack = " ".join(
            [
                destination_name,
                destination_kind,
                destination_description,
            ]
        ).lower()
        if normalized_query not in haystack:
            continue

        key = (
            destination.get("name", "").strip().lower(),
            round(destination.get("latitude", 0), 4),
            round(destination.get("longitude", 0), 4),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized_name = destination_name.lower()
        relevance_rank = 100
        if normalized_name == normalized_query:
            relevance_rank = 0
        elif normalized_name.startswith(normalized_query):
            relevance_rank = 10
        elif f" {normalized_query}" in normalized_name:
            relevance_rank = 20
        elif normalized_query in normalized_name:
            relevance_rank = 30
        elif normalized_query in destination_kind.lower():
            relevance_rank = 45
        elif normalized_query in destination_description.lower():
            relevance_rank = 55

        scored_matches.append(
            (
                relevance_rank,
                len(destination_name),
                {
                    "id": destination.get("id") or f"db-{len(scored_matches) + 1}",
                    "name": destination["name"],
                    "kind": destination_kind or "Destination",
                    "description": destination_description or "",
                    "latitude": destination["latitude"],
                    "longitude": destination["longitude"],
                    "source": "safepassage-db",
                },
            )
        )

    scored_matches.sort(key=lambda item: (item[0], item[1], item[2]["name"].lower(), item[2]["kind"].lower()))
    return [item[2] for item in scored_matches[:limit]]


@lru_cache(maxsize=128)
def _remote_india_place_matches(query, limit=8):
    if not (query or "").strip():
        return []

    endpoint = "https://nominatim.openstreetmap.org/search"
    query_string = urlencode(
        {
            "q": query,
            "countrycodes": "in",
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": limit,
        }
    )
    request = Request(
        f"{endpoint}?{query_string}",
        headers={
            "User-Agent": "SafePassage/1.0 (tourist-route-search)",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=1.2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return []

    results = []
    for index, item in enumerate(payload, start=1):
        try:
            latitude = round(float(item["lat"]), 6)
            longitude = round(float(item["lon"]), 6)
        except (KeyError, TypeError, ValueError):
            continue

        display_name = (item.get("display_name") or "").strip()
        short_name = (display_name.split(",")[0] if display_name else "").strip() or "Selected place"
        address = item.get("address") or {}
        state = address.get("state") or address.get("region") or ""
        results.append(
            {
                "id": f"osm-{index}-{latitude}-{longitude}",
                "name": short_name,
                "kind": address.get("city") or address.get("town") or address.get("village") or address.get("suburb") or "India location",
                "description": display_name if not state else f"{display_name}",
                "latitude": latitude,
                "longitude": longitude,
                "source": "openstreetmap",
            }
        )
    return results


def _has_primary_local_place_match(local_results, normalized_query):
    for result in local_results:
        normalized_name = (result.get("name") or "").strip().lower()
        if not normalized_name:
            continue
        if normalized_name == normalized_query or normalized_name.startswith(normalized_query):
            return True
    return False


def _search_india_places(query, limit=10):
    local_results = _local_india_place_matches(query, limit=limit)
    normalized_query = (query or "").strip().lower()
    has_primary_local_match = _has_primary_local_place_match(local_results, normalized_query)
    should_use_remote = (
        len(normalized_query) >= 3
        and (not local_results or not has_primary_local_match)
    )
    remote_results = _remote_india_place_matches(query, limit=limit) if should_use_remote else []

    merged = []
    seen = set()
    ordered_results = local_results + remote_results if has_primary_local_match else remote_results + local_results
    for item in ordered_results:
        key = (
            item.get("name", "").strip().lower(),
            round(item.get("latitude", 0), 4),
            round(item.get("longitude", 0), 4),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _resolve_route_destination(destination_reference):
    normalized_reference = (destination_reference or "").strip().lower()
    if not normalized_reference:
        return None

    for destination in _build_route_destination_catalog():
        if destination["id"].lower() == normalized_reference:
            return destination
        if destination["name"].strip().lower() == normalized_reference:
            return destination
    return None


def _dedupe_route_points(route_points):
    deduped_points = []
    for point_lat, point_lng in route_points:
        rounded_point = (round(point_lat, 6), round(point_lng, 6))
        if deduped_points and deduped_points[-1] == rounded_point:
            continue
        deduped_points.append(rounded_point)
    return deduped_points


def _route_profile_order(route_points):
    total_distance_km = 0
    for index in range(1, len(route_points)):
        prev_lat, prev_lng = route_points[index - 1]
        point_lat, point_lng = route_points[index]
        total_distance_km += _haversine_km(prev_lat, prev_lng, point_lat, point_lng)
    if total_distance_km <= 2.5:
        return ("foot", "driving")
    return ("driving", "foot")


@lru_cache(maxsize=256)
def _fetch_osrm_route_geometry(route_points_key, profile):
    if not _remote_service_enabled() or len(route_points_key) < 2:
        return None

    coordinate_string = ";".join(
        f"{point_lng:.6f},{point_lat:.6f}"
        for point_lat, point_lng in route_points_key
    )
    request = Request(
        (
            f"https://router.project-osrm.org/route/v1/{profile}/{coordinate_string}"
            "?overview=full&alternatives=false&steps=false&geometries=geojson&continue_straight=false"
        ),
        headers={
            "User-Agent": "SafePassage/1.0 (tourist-safe-route)",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=2.4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return None

    best_route = (payload.get("routes") or [None])[0]
    geometry = ((best_route or {}).get("geometry") or {}).get("coordinates") or []
    if len(geometry) < 2:
        return None

    live_polyline = []
    for point_lng, point_lat in geometry:
        try:
            live_polyline.append([round(float(point_lat), 6), round(float(point_lng), 6)])
        except (TypeError, ValueError):
            continue
    if len(live_polyline) < 2:
        return None

    distance_m = (best_route or {}).get("distance")
    duration_s = (best_route or {}).get("duration")
    return {
        "polyline": live_polyline,
        "profile": profile,
        "distance_km": round(float(distance_m) / 1000, 2) if distance_m is not None else None,
        "duration_min": max(1, round(float(duration_s) / 60)) if duration_s is not None else None,
        "source": "live-road",
    }


def _build_live_route_geometry(route_points):
    normalized_points = tuple(_dedupe_route_points(route_points))
    if len(normalized_points) < 2:
        return None

    for profile in _route_profile_order(normalized_points):
        live_route = _fetch_osrm_route_geometry(normalized_points, profile)
        if live_route:
            return live_route
    return None


def _perpendicular_detour_point(source_lat, source_lng, dest_lat, dest_lng, scale=0.12):
    mid_lat = (source_lat + dest_lat) / 2
    mid_lng = (source_lng + dest_lng) / 2
    detour_lat = mid_lat + ((dest_lng - source_lng) * scale)
    detour_lng = mid_lng - ((dest_lat - source_lat) * scale)
    return round(detour_lat, 6), round(detour_lng, 6)


def _hotspot_avoidance_point(source_lat, source_lng, dest_lat, dest_lng, corridor_hotspots, factor=0.3):
    if not corridor_hotspots:
        return _perpendicular_detour_point(source_lat, source_lng, dest_lat, dest_lng, scale=factor / 2)

    mid_lat = (source_lat + dest_lat) / 2
    mid_lng = (source_lng + dest_lng) / 2
    hotspot_sample = corridor_hotspots[:3]
    avg_zone_lat = sum(zone.latitude for zone, _ in hotspot_sample) / len(hotspot_sample)
    avg_zone_lng = sum(zone.longitude for zone, _ in hotspot_sample) / len(hotspot_sample)
    detour_lat = mid_lat + ((mid_lat - avg_zone_lat) * factor)
    detour_lng = mid_lng + ((mid_lng - avg_zone_lng) * factor)
    return round(detour_lat, 6), round(detour_lng, 6)


def _build_route_option_payload(
    tier,
    route_points,
    source_lat,
    source_lng,
    dest_lat,
    dest_lng,
    corridor_resources,
    corridor_hotspots,
    destination_label=None,
):
    tier_meta = {
        "low": {
            "title": "Low Risk Route",
            "strategy": "safe-haven-priority",
            "strategy_label": "Safest path",
            "color": "#16a34a",
            "score_bias": -10,
            "summary_note": "Prioritizes verified safe havens and stronger avoidance around risk clusters.",
        },
        "medium": {
            "title": "Medium Risk Route",
            "strategy": "balanced-monitoring",
            "strategy_label": "Balanced path",
            "color": "#f59e0b",
            "score_bias": 0,
            "summary_note": "Balances travel distance with moderate detours around active night-risk areas.",
        },
        "high": {
            "title": "High Risk Route",
            "strategy": "direct-fastest",
            "strategy_label": "Direct path",
            "color": "#ef4444",
            "score_bias": 10,
            "summary_note": "Fastest route with fewer safety detours. Keep this only as a last-choice option.",
        },
    }[tier]

    route_points = _dedupe_route_points(route_points)
    route_samples = []
    total_distance_km = 0
    for index, (point_lat, point_lng) in enumerate(route_points):
        payload = _build_risk_payload(point_lat, point_lng)
        point_label = payload["location"]
        if index == len(route_points) - 1 and destination_label:
            point_label = destination_label
        route_samples.append(
            {
                "order": index + 1,
                "latitude": round(point_lat, 6),
                "longitude": round(point_lng, 6),
                "risk_score": payload["risk_score"],
                "risk_label": payload["risk_label"],
                "advice": payload["advice"],
                "location": point_label,
            }
        )
        if index > 0:
            prev_lat, prev_lng = route_points[index - 1]
            total_distance_km += _haversine_km(prev_lat, prev_lng, point_lat, point_lng)

    scored_samples = [sample["risk_score"] for sample in route_samples if sample["risk_score"] is not None]
    if scored_samples:
        raw_score = int(sum(scored_samples) / len(scored_samples))
        haven_bonus = min(len(corridor_resources[:2]) * 4, 8)
        hotspot_penalty = min(sum(zone.risk_score for zone, _ in corridor_hotspots[:2]) // 35, 12) if corridor_hotspots else 0
        adjusted_score = raw_score + tier_meta["score_bias"]
        if tier == "low":
            adjusted_score -= haven_bonus
            adjusted_score -= 4 if corridor_hotspots else 0
        elif tier == "medium":
            adjusted_score += max(hotspot_penalty - 3, 0)
        else:
            adjusted_score += hotspot_penalty
        overall_risk_score = max(0, min(100, adjusted_score))
        overall_risk_label = _normalize_risk_label(overall_risk_score)
    else:
        overall_risk_score = None
        overall_risk_label = "UNAVAILABLE"

    advisories = []
    for advice_text in [tier_meta["summary_note"], *[sample["advice"] for sample in route_samples]]:
        if advice_text and advice_text not in advisories:
            advisories.append(advice_text)

    waypoint_polyline = [[sample["latitude"], sample["longitude"]] for sample in route_samples]
    live_route = _build_live_route_geometry(route_points)
    display_polyline = live_route["polyline"] if live_route else waypoint_polyline
    display_distance_km = live_route["distance_km"] if live_route and live_route.get("distance_km") is not None else round(total_distance_km, 2)

    if live_route and live_route.get("duration_min") is not None:
        profile_label = "walking" if live_route["profile"] == "foot" else "road"
        live_route_note = f"Live {profile_label} route aligned. Approx {live_route['duration_min']} min travel time."
        if live_route_note not in advisories:
            advisories.insert(0, live_route_note)

    return {
        "id": tier,
        "tier": tier,
        "tier_label": tier.upper(),
        "title": tier_meta["title"],
        "strategy": tier_meta["strategy"],
        "strategy_label": tier_meta["strategy_label"],
        "color": tier_meta["color"],
        "route": route_samples,
        "polyline": display_polyline,
        "waypoint_polyline": waypoint_polyline,
        "polyline_source": live_route["source"] if live_route else "safety-corridor",
        "live_route_meta": {
            "profile": live_route["profile"],
            "distance_km": live_route["distance_km"],
            "duration_min": live_route["duration_min"],
        } if live_route else None,
        "route_summary": {
            "overall_risk_score": overall_risk_score,
            "overall_risk_label": overall_risk_label,
            "distance_km": display_distance_km,
            "corridor_distance_km": round(total_distance_km, 2),
            "advisories": advisories[:4],
            "data_available": bool(scored_samples),
            "hotspot_count": len(corridor_hotspots[:3]),
            "safe_haven_count": len(corridor_resources[:3]),
        },
        "safe_havens": [
            {
                "name": haven.name,
                "type": haven.get_type_display(),
                "latitude": haven.latitude,
                "longitude": haven.longitude,
                "distance_km": distance_km,
                "address": haven.address,
            }
            for haven, distance_km in corridor_resources[:3]
        ],
        "risk_hotspots": [
            {
                "risk_type": zone.get_risk_type_display(),
                "risk_score": zone.risk_score,
                "latitude": zone.latitude,
                "longitude": zone.longitude,
                "description": zone.description,
                "distance_km": distance_km,
            }
            for zone, distance_km in corridor_hotspots[:3]
        ],
    }


def _normalize_route_option_order(route_options):
    option_by_id = {option["id"]: option for option in route_options}
    low_option = option_by_id.get("low")
    medium_option = option_by_id.get("medium")
    high_option = option_by_id.get("high")
    ordered_options = [item for item in (low_option, medium_option, high_option) if item]

    scored_options = [item for item in ordered_options if item["route_summary"]["overall_risk_score"] is not None]
    if len(scored_options) < 2:
        return ordered_options

    previous_score = None
    for index, option in enumerate(ordered_options):
        score = option["route_summary"]["overall_risk_score"]
        if score is None:
            continue
        if previous_score is None:
            previous_score = score
            continue
        minimum_gap = 5 if option["id"] != "medium" else 4
        if score <= previous_score:
            score = min(100, previous_score + minimum_gap)
            option["route_summary"]["overall_risk_score"] = score
            option["route_summary"]["overall_risk_label"] = _normalize_risk_label(score)
        previous_score = score
    return ordered_options


def _build_safe_route_payload(user, source_lat, source_lng, dest_lat, dest_lng, destination_label=None):
    mid_lat = (source_lat + dest_lat) / 2
    mid_lng = (source_lng + dest_lng) / 2
    corridor_resources = _nearby_records(SafeHaven.objects.all(), mid_lat, mid_lng, radius_km=20)
    corridor_hotspots = _nearby_records(
        RiskZone.objects.filter(risk_score__gte=45),
        mid_lat,
        mid_lng,
        radius_km=20,
    )

    nearest_haven = corridor_resources[0][0] if corridor_resources else None
    gentle_detour = _hotspot_avoidance_point(source_lat, source_lng, dest_lat, dest_lng, corridor_hotspots, factor=0.18)
    strong_detour = _hotspot_avoidance_point(source_lat, source_lng, dest_lat, dest_lng, corridor_hotspots, factor=0.38)
    fallback_detour = _perpendicular_detour_point(source_lat, source_lng, dest_lat, dest_lng, scale=0.16)

    high_route_points = [
        (source_lat, source_lng),
        (dest_lat, dest_lng),
    ]

    medium_route_points = [(source_lat, source_lng)]
    if corridor_hotspots:
        medium_route_points.append(gentle_detour)
    elif nearest_haven and corridor_resources[0][1] <= 9:
        medium_route_points.append((nearest_haven.latitude, nearest_haven.longitude))
    else:
        medium_route_points.append(fallback_detour)
    medium_route_points.append((dest_lat, dest_lng))

    low_route_points = [(source_lat, source_lng)]
    if nearest_haven and corridor_resources[0][1] <= 12:
        low_route_points.append((nearest_haven.latitude, nearest_haven.longitude))
    low_route_points.append(strong_detour if corridor_hotspots else _perpendicular_detour_point(source_lat, source_lng, dest_lat, dest_lng, scale=0.24))
    low_route_points.append((dest_lat, dest_lng))

    route_options = _normalize_route_option_order(
        [
            _build_route_option_payload(
                "low",
                low_route_points,
                source_lat,
                source_lng,
                dest_lat,
                dest_lng,
                corridor_resources,
                corridor_hotspots,
                destination_label=destination_label,
            ),
            _build_route_option_payload(
                "medium",
                medium_route_points,
                source_lat,
                source_lng,
                dest_lat,
                dest_lng,
                corridor_resources,
                corridor_hotspots,
                destination_label=destination_label,
            ),
            _build_route_option_payload(
                "high",
                high_route_points,
                source_lat,
                source_lng,
                dest_lat,
                dest_lng,
                corridor_resources,
                corridor_hotspots,
                destination_label=destination_label,
            ),
        ]
    )
    default_route = next((option for option in route_options if option["id"] == "low"), route_options[0])

    return {
        "status": "success",
        "strategy": default_route["strategy"],
        "strategy_label": default_route["strategy_label"],
        "corridor_hotspot_definition": (
            "Risk hotspots on corridor are stored risk zones within the active travel corridor, "
            "such as high-crime clusters, scam-prone stretches, poorly lit areas, or accident-prone segments."
        ),
        "source": {
            "latitude": round(source_lat, 6),
            "longitude": round(source_lng, 6),
            "name": _compose_location_label(source_lat, source_lng),
            "display_label": _compose_location_label(source_lat, source_lng, include_coordinates=True),
        },
        "destination": {
            "latitude": round(dest_lat, 6),
            "longitude": round(dest_lng, 6),
            "name": destination_label or _resolve_location_name(dest_lat, dest_lng),
            "display_label": _compose_location_label(dest_lat, dest_lng, include_coordinates=True),
        },
        "default_route_tier": default_route["id"],
        "route_options": route_options,
        "route": default_route["route"],
        "polyline": default_route["polyline"],
        "route_summary": default_route["route_summary"],
        "safe_havens": default_route["safe_havens"],
        "risk_hotspots": default_route["risk_hotspots"],
    }


def _normalize_nationality(raw_value):
    value = (raw_value or "").strip().lower()
    if value in {"american", "usa", "us", "united states", "united states of america"}:
        return "us"
    if value in {"british", "uk", "united kingdom", "england"}:
        return "uk"
    if value in {"canadian", "canada"}:
        return "canada"
    if value in {"australian", "australia"}:
        return "australia"
    return None


def _default_embassy_payload(request, lat=None, lng=None):
    profile = TouristProfile.objects.filter(user=request.user).first()
    nationality_key = _normalize_nationality(profile.nationality if profile else "")
    if nationality_key and nationality_key in EMBASSY_DIRECTORY:
        embassy = EMBASSY_DIRECTORY[nationality_key].copy()
    else:
        embassy = {
            "country": profile.nationality if profile and profile.nationality else "Not specified",
            "embassy_name": "Embassy details need your nationality",
            "city": "",
            "phone": "",
            "emergency_number": "112",
            "address": "Update your nationality in the tourist profile to load the correct embassy contact.",
        }

    if lat is not None and lng is not None:
        embassy["zone_alert"] = _build_risk_payload(lat, lng)["advice"]
        embassy["location"] = _resolve_location_name(lat, lng)
    else:
        embassy["zone_alert"] = "Keep your embassy contact saved offline before longer intercity travel."
        embassy["location"] = None
    return embassy


def _live_translate_text(text, source_language, target_language):
    if not _remote_service_enabled():
        return "", "unavailable"

    source = _normalize_language_code(source_language, default="en")
    target = _normalize_language_code(target_language, default="en")
    if not text.strip():
        return "", "empty"
    if source == target:
        return text, "identity"

    # Try Unofficial Google Translate API first (Fast and reliable for single requests)
    try:
        google_query = {
            "client": "gtx",
            "sl": source,
            "tl": target,
            "dt": "t",
            "q": text,
        }
        google_url = f"https://translate.googleapis.com/translate_a/single?{urlencode(google_query)}"
        google_request = Request(
            google_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            },
        )
        with urlopen(google_request, timeout=3) as res:
            data = json.loads(res.read().decode("utf-8"))
            if data and data[0] and data[0][0] and data[0][0][0]:
                translated = ""
                for segment in data[0]:
                    if segment[0]:
                        translated += segment[0]
                return html.unescape(translated), "live-api"
    except Exception:
        pass  # Fallback to MyMemory

    # Fallback to MyMemory Translation Service
    query = {
        "q": text,
        "langpair": f"{source}|{target}",
    }
    contact_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "EMAIL_HOST_USER", "")
    if contact_email and "@" in contact_email and "safepassage-india.org" not in contact_email and "yourgmail@gmail.com" not in contact_email:
        query["de"] = contact_email

    request = Request(
        f"https://api.mymemory.translated.net/get?{urlencode(query)}",
        headers={
            "User-Agent": "SafePassage/1.0 (tourist-translation)",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            response_data = payload.get("responseData") or {}
            translated_text = html.unescape((response_data.get("translatedText") or "").strip())
            if translated_text and not any(err in translated_text.upper() for err in ["LIMIT EXCEEDED", "INVALID EMAIL", "INVALID PARAMETER"]):
                return translated_text, "live-api"
    except Exception:
        pass

    return "", "unavailable"


def _translate_phrase(text, language):
    normalized = re.sub(r"[^a-z0-9\s]", "", (text or "").strip().lower())
    normalized = " ".join(normalized.split())
    if not normalized:
        return "", "empty"

    normalized_language = _normalize_language_code(language, default="en")
    if normalized_language == "en":
        return text, "identity"

    phrase_key = normalized if normalized in EMERGENCY_PHRASEBOOK else None
    match_mode = "phrasebook"
    if phrase_key is None:
        for candidate, matchers in EMERGENCY_PHRASE_MATCHERS.items():
            if any(re.search(pattern, normalized) for pattern in matchers):
                phrase_key = candidate
                match_mode = "intent-match"
                break

    phrase_entry = EMERGENCY_PHRASEBOOK.get(phrase_key or "", {})
    if normalized_language in phrase_entry:
        return phrase_entry[normalized_language], match_mode

    live_translation, live_mode = _live_translate_text(text, "en", normalized_language)
    if live_translation:
        return live_translation, live_mode
    return "", "unavailable"


def _dispatch_emergency_alert(user, lat, lng, mode, request=None):
    alert = EmergencyAlert.objects.create(
        user=user,
        latitude=lat,
        longitude=lng,
        mode=mode,
    )

    contacts = _dispatch_contact_rows(user)
    contact_deliveries = []
    sms_contacts = 0
    whatsapp_contacts = 0
    email_recipients = []
    for contact in contacts:
        delivery_channels = []
        if contact.get("sms_enabled"):
            delivery_channels.append("SMS")
            sms_contacts += 1
        if contact.get("whatsapp_enabled"):
            delivery_channels.append("WhatsApp")
            whatsapp_contacts += 1
        if contact.get("email"):
            email_recipients.append(contact["email"])
            delivery_channels.append("Email")
        if delivery_channels:
            contact_deliveries.append(
                {
                    "name": contact.get("name") or "Emergency contact",
                    "phone": contact.get("phone") or "",
                    "relationship": contact.get("relationship") or "Emergency contact",
                    "is_primary": bool(contact.get("is_primary")),
                    "channels": delivery_channels,
                    "source": contact.get("source") or "emergency-contact",
                }
            )

    notified_contacts = len(contact_deliveries)

    subject = "🚨 SOS Alert Notification"
    from django.utils import timezone as tz
    time_str = tz.localtime(alert.timestamp).strftime("%d %B %Y, %I:%M %p")
    display_name = (user.first_name or "").strip() or user.username
    message = (
        f"🚨 SOS Alert Notification\n\n"
        f"This is an automated alert from SafePassage.\n\n"
        f"An SOS signal has been triggered by the user. Immediate attention may be required.\n\n"
        f"----------------------------------------\n"
        f"User Information:\n"
        f"• Name: {display_name}\n"
        f"• Email: {user.email}\n\n"
        f"Location:\n"
        f"• https://maps.google.com/?q={lat},{lng}\n\n"
        f"Timestamp:\n"
        f"• {time_str}\n\n"
        f"----------------------------------------\n"
        f"This alert indicates that the user may be in a potentially unsafe situation.\n\n"
        f"Kindly reach out to the user or initiate appropriate emergency response.\n\n"
        f"This is an automated message generated by the SafePassage Safety System."
    )

    all_recipients = list(dict.fromkeys(email_recipients))
    if not all_recipients and (user.email or "").strip():
        all_recipients = [user.email.strip()]

    if request:
        confirm_url = request.build_absolute_uri(reverse('api_sos_acknowledge', kwargs={'token': alert.token}))
        message += (
            f"\n\n"
            f"----------------------------------------\n"
            f"CONFIRM RECEIPT:\n"
            f"Please click the link below to confirm you have received this alert:\n"
            f"{confirm_url}\n"
            f"----------------------------------------"
        )

    _send_broadcast_notifications(
        subject=subject,
        message_body=message,
        sender=_notification_sender_email(),
        recipient_list=all_recipients,
    )

    translated_message, translation_mode = _translate_phrase(
        "Help me, I am in danger",
        translation.get_language() or "en",
    )

    return {
        "status": "success",
        "alert_id": alert.id,
        "mode": mode,
        "notified_contacts": notified_contacts,
        "delivery_channels": {
            "sms_contacts": sms_contacts,
            "whatsapp_contacts": whatsapp_contacts,
            "email_contacts": len(email_recipients),
            "total_channel_dispatches": sms_contacts + whatsapp_contacts + len(email_recipients),
        },
        "contact_deliveries": contact_deliveries,
        "authorities_notified": True,
        "translated_message": translated_message,
        "translation_mode": translation_mode,
        "timestamp": alert.timestamp.isoformat(),
    }

def _dashboard_url_for_user(user):
    if not user or not user.is_authenticated:
        return "/login/"
    if user.role == "tourist":
        return "/dashboard/?mode=tourist"
    if user.role == "worker":
        return "/worker/dashboard/"
    if user.role == "admin" or user.is_superuser:
        return "/admin/dashboard/"
    if user.role == "employer":
        return "/employer/dashboard/"
    return "/login/"


def _map_url_for_user(user):
    if not user or not user.is_authenticated:
        return "/map/"
    if user.role == "worker":
        return "/worker/map/"
    if user.role == "admin" or user.is_superuser:
        return "/admin/risk-monitor/"
    return "/map/"


def _safe_route_url_for_user(user):
    if not user or not user.is_authenticated:
        return "/map/"
    if user.role == "worker":
        return "/worker/safe-route/"
    if user.role == "tourist":
        return "/map/?tab=routes"
    if user.role == "admin" or user.is_superuser:
        return "/admin/risk-monitor/"
    return _dashboard_url_for_user(user)


def _sos_url_for_user(user):
    if not user or not user.is_authenticated:
        return "/login/"
    if user.role == "worker":
        return "/worker/sos/"
    if user.role == "tourist":
        return "/sos/"
    return _dashboard_url_for_user(user)


def _alerts_url_for_user(user):
    if not user or not user.is_authenticated:
        return "/login/"
    if user.role == "worker":
        return "/worker/alerts/"
    if user.role == "tourist":
        return "/alerts/"
    if user.role == "admin" or user.is_superuser:
        return "/admin/notifications/"
    return _dashboard_url_for_user(user)


def _build_landing_alert_feed(limit=5):
    alert_feed = []

    for alert in EmergencyAlert.objects.select_related("user").order_by("-timestamp")[:limit]:
        alert_feed.append(
            {
                "kind": "SOS",
                "title": f"{alert.user.get_role_display()} SOS",
                "detail": f"{alert.mode.title()} alert from {alert.user.first_name or alert.user.username}",
                "status": alert.status or "Active",
                "timestamp": alert.timestamp,
                "timestamp_label": _time_since_label(alert.timestamp),
                "priority_score": 100 if (alert.status or "").strip().lower() != "resolved" else 65,
            }
        )

    for report in IncidentReport.objects.select_related("user").order_by("-created_at")[:limit]:
        alert_feed.append(
            {
                "kind": "Incident",
                "title": report.location_label or report.get_incident_type_display(),
                "detail": report.description,
                "status": report.get_incident_type_display(),
                "timestamp": report.created_at,
                "timestamp_label": _time_since_label(report.created_at),
                "priority_score": report.risk_score_snapshot or 40,
            }
        )

    alert_feed.sort(
        key=lambda item: (
            item["timestamp"] is not None,
            item["timestamp"] or timezone.now() - timedelta(days=3650),
            item["priority_score"],
        ),
        reverse=True,
    )
    return alert_feed[:limit]


def _build_landing_page_context(request):
    tracked_cutoff = timezone.now() - timedelta(minutes=20)
    recent_incident_cutoff = timezone.now() - timedelta(days=7)

    active_tracker_count = (
        UserLocation.objects.filter(timestamp__gte=tracked_cutoff)
        .values("user_id")
        .distinct()
        .count()
    )
    active_sos_count = EmergencyAlert.objects.exclude(status__iexact="resolved").count()
    active_shift_count = Shift.objects.filter(status="active").count()
    recent_incident_count = IncidentReport.objects.filter(created_at__gte=recent_incident_cutoff).count()

    preview_zones = [
        {
            "id": zone.id,
            "latitude": zone.latitude,
            "longitude": zone.longitude,
            "city": zone.city,
            "risk_type": zone.get_risk_type_display(),
            "risk_score": zone.risk_score,
            "risk_label": _normalize_risk_label(zone.risk_score),
            "description": zone.description,
        }
        for zone in RiskZone.objects.order_by("-risk_score", "city", "id")[:12]
    ]

    initial_lat = None
    initial_lng = None
    initial_location_label = None
    if request.user.is_authenticated and request.user.role in {"tourist", "worker"}:
        initial_lat, initial_lng = _resolve_user_coordinates(request.user)
        if initial_lat is not None and initial_lng is not None:
            initial_location_label = _resolve_location_name(initial_lat, initial_lng)

    current_role = request.user.role if request.user.is_authenticated else ""

    return {
        "landing_stats": {
            "protected_users": SafePassageUser.objects.filter(role__in=["tourist", "worker"], is_active=True).count(),
            "active_trackers": active_tracker_count,
            "active_sos_alerts": active_sos_count,
            "verified_safe_havens": SafeHaven.objects.count(),
            "monitored_risk_zones": RiskZone.objects.count(),
            "recent_incidents": recent_incident_count,
            "cultural_guides": CulturalGuide.objects.count(),
            "active_shifts": active_shift_count,
        },
        "landing_preview_zones": preview_zones,
        "landing_alert_feed": _build_landing_alert_feed(),
        "landing_routes": {
            "map": _map_url_for_user(request.user),
            "safe_route": _safe_route_url_for_user(request.user),
            "dashboard": _dashboard_url_for_user(request.user),
            "sos": _sos_url_for_user(request.user),
            "alerts": _alerts_url_for_user(request.user),
            "tourist_dashboard": "/dashboard/?mode=tourist",
            "worker_dashboard": "/worker/dashboard/",
            "login": "/login/",
            "register": "/register/",
        },
        "landing_config": {
            "is_authenticated": request.user.is_authenticated,
            "current_role": current_role,
            "can_trigger_emergency": request.user.is_authenticated and current_role in {"tourist", "worker"},
            "can_access_tourist_mode": request.user.is_authenticated and current_role == "tourist",
            "can_access_worker_mode": request.user.is_authenticated and current_role == "worker",
            "risk_endpoint": "/api/get-risk-zones/",
            "emergency_endpoint": "/api/emergency/",
            "initial_latitude": initial_lat,
            "initial_longitude": initial_lng,
            "initial_location_label": initial_location_label,
        },
    }


# 🏠 Landing Page
def index(request):
    return render(request, "index.html", _build_landing_page_context(request))


def about(request):
    return render(request, "about.html")
# 📝 Register Page
def register(request):
    if request.method == "POST":
        full_name = (request.POST.get("full_name") or "").strip()
        email = (request.POST.get("email") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        role = (request.POST.get("role") or "").strip()
        password = request.POST.get("password") or ""
        confirm_password = request.POST.get("confirm_password") or ""

        # Prevent admin registration
        if role == "admin":
            messages.error(request, "Admin accounts cannot be created publicly.")
            return render(request, "register.html")

        # Strict Validations
        if not re.match(r"^[a-zA-Z\s]{3,}$", full_name):
            messages.error(request, "Full Name must contain only alphabets and be at least 3 characters.")
            return render(request, "register.html")

        if not re.match(r"^\d{10}$", phone):
            messages.error(request, "Enter a valid 10-digit phone number.")
            return render(request, "register.html")

        # Password Strength Check
        if (len(password) < 8 or
            not re.search(r"[A-Z]", password) or
            not re.search(r"[a-z]", password) or
            not re.search(r"\d", password) or
            not re.search(r"[@$!%*?&]", password)):
            messages.error(request, "Password must be at least 8 characters and include uppercase, lowercase, number, and special character.")
            return render(request, "register.html")

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "register.html")

        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
            messages.error(request, "Enter a valid email address.")
            return render(request, "register.html")

        if SafePassageUser.objects.filter(email=email).exists():
            messages.error(request, "Email already registered.")
            return render(request, "register.html")
        # Create user
        user = SafePassageUser.objects.create_user(
            username=email, # Using email as username
            email=email,
            password=password,
            first_name=full_name,
            role=role,
            phone=phone
        )
        user.save()

        messages.success(request, "Registration successful. Please login to continue.")
        return redirect("login")

    return render(request, "register.html")

# 🔐 Login Page
def user_login(request):
    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")
        role = request.POST.get("role")

        user = authenticate(request, username=email, password=password)

        if user is not None:
            if user.role == role:
                login(request, user)
                # Redirection based on role
                if user.role == "tourist":
                    return redirect("/dashboard/?mode=tourist")
                elif user.role == "worker":
                    return redirect("worker_dashboard")
                elif user.role == "employer":
                    return redirect("employer_dashboard")
                elif user.role == "admin" or user.is_superuser:
                    return redirect("admin_dashboard")
            else:
                messages.error(request, f"Incorrect role selected for this account.")
        else:
            messages.error(request, "Invalid email or password.")

    return render(request, "login.html")


# 🚶 Logout
def user_logout(request):
    logout(request)
    return redirect("index")


# 🗺️ Tourist Dashboard
@login_required(login_url='login')
def tourist_dashboard(request):
    if request.user.role != 'tourist' and not request.user.is_superuser:
        return render(request, "unauthorized.html")
    return render(
        request,
        "tourist_dashboard.html",
        {
            "emergency_contacts_count": EmergencyContact.objects.filter(user=request.user).count(),
        },
    )


@tourist_required
def tourist_alerts(request):
    return render(request, "tourist_alerts.html")


@tourist_required
def tourist_translate(request):
    quick_phrases = ["Help", "Police", "Hospital", "Emergency", "Danger", "Lost", "Water", "Medicine", "Fire", "Stop"]
    return render(request, "tourist_translate.html", {"quick_phrases": quick_phrases})


# Tourist Emergency
@tourist_required
def tourist_emergency(request):
    return render(
        request,
        "tourist_emergency.html",
        {
            "emergency_contacts": EmergencyContact.objects.filter(user=request.user).order_by("-is_primary", "name"),
            "emergency_contacts_count": EmergencyContact.objects.filter(user=request.user).count(),
        },
    )


@tourist_required
def tourist_dashboard_hub(request):
    requested_mode = request.GET.get("mode")
    if requested_mode and requested_mode != "tourist":
        return redirect("/dashboard/?mode=tourist")
    return tourist_dashboard(request)


# 📊 Tourist Risk Map
@login_required(login_url='login')
def tourist_risk_map(request):
    if request.user.role != 'tourist' and not request.user.is_superuser:
        return render(request, "unauthorized.html")
    
    risk_zones = RiskZone.objects.all()
    return render(request, "tourist_risk_map.html", {
        "risk_zones": risk_zones,
        "initial_tab": "routes" if request.GET.get("tab") == "routes" else "live",
    })


@tourist_required
def tourist_safe_route(request):
    return redirect("/map/?tab=routes")


@tourist_required
def tourist_scam_alerts(request):
    return render(request, "tourist_scam_alerts.html")


@tourist_required
def tourist_emergency_contacts(request):
    return render(
        request,
        "tourist_emergency_contacts.html",
        {
            "emergency_contacts_count": EmergencyContact.objects.filter(user=request.user).count(),
            "emergency_contacts": EmergencyContact.objects.filter(user=request.user).order_by("-is_primary", "name"),
        },
    )

# 🌍 Cultural Guide
@login_required(login_url='login')
def cultural_guide(request):
    if request.user.role != 'tourist' and not request.user.is_superuser:
        return render(request, "unauthorized.html")
    return render(request, "tourist/cultural_guide.html")

# Tourist Profile
@login_required(login_url='login')
def tourist_profile(request):
    if request.user.role != 'tourist' and not request.user.is_superuser:
        return render(request, "unauthorized.html")
    
    # Get or create tourist profile
    profile, created = TouristProfile.objects.get_or_create(
        user=request.user,
        defaults={
            'full_name': f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username,
            'nationality': ''
        }
    )
    
    # Get or create journey details
    journey, created = JourneyDetail.objects.get_or_create(
        user=request.user,
        defaults={
            'arrival_date': timezone.now().date(),
            'departure_date': (timezone.now() + timezone.timedelta(days=14)).date(),
            'current_location': ''
        }
    )
    
    # Get emergency contacts
    emergency_contacts = EmergencyContact.objects.filter(user=request.user).order_by('-is_primary', 'name')
    
    context = {
        'profile': profile,
        'journey': journey,
        'emergency_contacts': emergency_contacts,
    }
    
    return render(request, "tourist_profile.html", context)


@login_required
def api_predict_risk(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _parse_coordinates(request.GET)
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Latitude and longitude are required."}, status=400)

    payload = _build_risk_payload(lat, lng)
    payload["status"] = "success"
    payload["coordinates"] = {"latitude": lat, "longitude": lng}
    return JsonResponse(payload)


@login_required
def api_safe_route(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    payload = _load_request_payload(request) if request.method == "POST" else request.GET
    destination_label = (
        payload.get("destination_label")
        or payload.get("destination_name")
        or payload.get("place_name")
    )

    source_lat, source_lng = _parse_coordinates(
        {
            "lat": payload.get("source_lat") or payload.get("sourceLatitude") or payload.get("lat"),
            "lng": payload.get("source_lng") or payload.get("sourceLongitude") or payload.get("lng"),
        }
    )
    if source_lat is None or source_lng is None:
        source_lat, source_lng = _resolve_user_coordinates(request.user)

    dest_lat, dest_lng = _parse_coordinates(
        {
            "lat": payload.get("dest_lat") or payload.get("destination_lat") or payload.get("destinationLatitude"),
            "lng": payload.get("dest_lng") or payload.get("destination_lng") or payload.get("destinationLongitude"),
        }
    )

    if dest_lat is None or dest_lng is None:
        destination_place = _resolve_route_destination(
            payload.get("destination_place")
            or payload.get("destination_place_id")
            or payload.get("destination_place_name")
            or destination_label
        )
        if destination_place:
            dest_lat = destination_place["latitude"]
            dest_lng = destination_place["longitude"]
            destination_label = destination_place["name"]

    if source_lat is None or source_lng is None:
        return JsonResponse({"status": "error", "message": "Source coordinates are required."}, status=400)
    if dest_lat is None or dest_lng is None:
        return JsonResponse({"status": "error", "message": "Choose a destination place or destination coordinates."}, status=400)

    return JsonResponse(
        _build_safe_route_payload(
            request.user,
            source_lat,
            source_lng,
            dest_lat,
            dest_lng,
            destination_label=destination_label,
        )
    )


@login_required
def api_place_search(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return JsonResponse(
            {
                "status": "error",
                "message": "Search query must be at least 2 characters.",
                "results": [],
            },
            status=400,
        )

    return JsonResponse(
        {
            "status": "success",
            "query": query,
            "results": _search_india_places(query, limit=10),
        }
    )


@login_required
def api_incidents(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _parse_coordinates(request.GET)
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Latitude and longitude are required."}, status=400)

    alerts = _build_incident_alerts(lat, lng, limit=5)

    return JsonResponse(
        {
            "status": "success",
            "count": len(alerts),
            "alerts": alerts,
        }
    )


@login_required
def api_alerts(request):
    return api_incidents(request)


@login_required
def api_weather_risk(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _parse_coordinates(request.GET)
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Latitude and longitude are required."}, status=400)

    payload = _weather_payload(lat, lng)
    payload["status"] = "success"
    payload["location"] = _resolve_location_name(lat, lng)
    return JsonResponse(payload)


@login_required
def api_cultural_advice(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _parse_coordinates(request.GET)
    language = request.GET.get("language") or translation.get_language() or "en"
    dos = [item["content"] for item in _collect_cultural_entries(language, "do")]
    donts = [item["content"] for item in _collect_cultural_entries(language, "dont")]
    risk_behaviors = [item["content"] for item in _collect_cultural_entries(language, "scam")]

    response_payload = {
        "status": "success",
        "language": language,
        "location": _resolve_location_name(lat, lng) if lat is not None and lng is not None else None,
        "data_available": bool(dos or donts or risk_behaviors),
        "dos": dos,
        "donts": donts,
        "risk_behaviors": risk_behaviors,
    }
    response_payload["scams"] = response_payload["risk_behaviors"]
    return JsonResponse(response_payload)


@login_required
def api_embassy_info(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _parse_coordinates(request.GET)
    embassy_payload = _default_embassy_payload(request, lat, lng)
    embassy_payload["status"] = "success"
    return JsonResponse(embassy_payload)


@login_required
def api_translate(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=405)

    payload = _load_request_payload(request)
    text = (payload.get("text") or "").strip()
    if not text:
        return JsonResponse({"status": "error", "message": "Text is required."}, status=400)

    targets = payload.get("target_languages") or payload.get("target_language") or ["hi", "ml"]
    if isinstance(targets, str):
        targets = [segment.strip() for segment in targets.split(",") if segment.strip()]
    if targets == ["all"]:
        targets = ["hi", "ml", "ta", "te", "kn"]

    translations = {}
    modes = set()
    for language in targets:
        translated_text, mode = _translate_phrase(text, language)
        translations[language] = translated_text
        modes.add(mode)

    response_payload = {
        "status": "success",
        "source_text": text,
        "translations": translations,
        "translation_mode": (
            "phrasebook"
            if modes == {"phrasebook"}
            else "intent-match"
            if modes == {"intent-match"}
            else "live-api"
            if modes == {"live-api"}
            else "identity"
            if modes == {"identity"}
            else "unavailable"
            if modes == {"unavailable"}
            else "mixed"
        ),
    }
    if len(translations) == 1:
        response_payload["translated_text"] = next(iter(translations.values()))
    if response_payload["translation_mode"] == "intent-match":
        response_payload["note"] = "Matched your message to the nearest supported emergency phrase."
    elif response_payload["translation_mode"] == "live-api":
        response_payload["note"] = "Translated using Google Translate services."
    elif response_payload["translation_mode"] == "identity":
        response_payload["note"] = "The selected output language is English, so the original text is shown."
    elif response_payload["translation_mode"] == "unavailable":
        response_payload["note"] = "Live translation is unavailable right now, and offline translation currently supports only emergency phrases like help, police, and ambulance requests."
    elif response_payload["translation_mode"] == "mixed":
        response_payload["note"] = "Some requested languages used offline emergency matching, while others used live translation or remain unavailable."
    return JsonResponse(response_payload)


@login_required
def api_report_incident(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=405)

    payload = _load_request_payload(request)
    lat, lng = _parse_coordinates(payload)
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Latitude and longitude are required."}, status=400)

    incident_type = payload.get("incident_type", "other")
    description = (payload.get("description") or "").strip()
    if not description:
        return JsonResponse({"status": "error", "message": "Description is required."}, status=400)

    risk_payload = _build_risk_payload(lat, lng)
    image_name = request.FILES.get("image").name if request.FILES.get("image") else ""

    report = IncidentReport.objects.create(
        user=request.user,
        incident_type=incident_type,
        description=description,
        location_label=payload.get("location_label") or payload.get("location") or risk_payload["location"],
        latitude=lat,
        longitude=lng,
        image_name=image_name,
        risk_score_snapshot=risk_payload["risk_score"],
    )

    return JsonResponse(
        {
            "status": "success",
            "report_id": report.id,
            "stored": True,
            "admin_alert_triggered": True,
            "map_update_triggered": True,
            "risk_score_snapshot": report.risk_score_snapshot,
        }
    )


@login_required
def api_emergency(request):
    guard_response = _travel_mode_api_guard(request)
    if guard_response:
        return guard_response

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=405)

    payload = _load_request_payload(request)
    lat, lng = _parse_coordinates(payload)
    location_source = "live"
    if lat is None or lng is None:
        lat, lng = _resolve_user_coordinates(request.user)
        location_source = "saved"
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Latitude and longitude are required."}, status=400)

    mode = (payload.get("mode") or "silent").lower()
    if mode not in {"silent", "loud"}:
        return JsonResponse({"status": "error", "message": "Unsupported emergency mode."}, status=400)

    UserLocation.objects.update_or_create(
        user=request.user,
        defaults={"latitude": lat, "longitude": lng},
    )
    response_payload = _dispatch_emergency_alert(request.user, lat, lng, mode, request=request)
    response_payload["location"] = _resolve_location_name(lat, lng)
    response_payload["coordinates"] = {
        "latitude": round(lat, 6),
        "longitude": round(lng, 6),
    }
    response_payload["location_source"] = location_source
    return JsonResponse(response_payload)

# API Endpoints for Profile Management
@login_required
def save_profile(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'})
    
    try:
        profile = TouristProfile.objects.get(user=request.user)
        full_name = (request.POST.get('full_name') or profile.full_name).strip()
        phone = (request.POST.get('phone') or request.user.phone).strip()

        # Strict validations
        if not re.match(r"^[a-zA-Z\s]{3,}$", full_name):
            return JsonResponse({'success': False, 'error': 'Full Name must contain only alphabets and be at least 3 characters.'})

        if not re.match(r"^\d{10}$", phone):
            return JsonResponse({'success': False, 'error': 'Enter a valid 10-digit phone number.'})

        profile.full_name = full_name
        profile.nationality = request.POST.get('nationality', profile.nationality)
        profile.date_of_birth = request.POST.get('date_of_birth') or None
        profile.blood_group = request.POST.get('blood_group', '')
        profile.allergies = request.POST.get('allergies', '')
        profile.medications = request.POST.get('medications', '')
        profile.save()
        
        # Update user phone and profile image
        user = request.user
        user.phone = phone
        if 'profile_image' in request.FILES:
            user.profile_image = request.FILES['profile_image']
        user.save()
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def save_journey(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'})
    
    try:
        arrival_date = request.POST.get('arrival_date')
        departure_date = request.POST.get('departure_date')

        if not arrival_date or not departure_date:
            return JsonResponse({'success': False, 'error': 'Arrival and Departure dates are required.'})

        try:
            timezone.datetime.strptime(arrival_date, '%Y-%m-%d')
            timezone.datetime.strptime(departure_date, '%Y-%m-%d')
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid date format. Please use YYYY-MM-DD.'})

        journey = JourneyDetail.objects.get(user=request.user)
        journey.arrival_date = arrival_date
        journey.departure_date = departure_date
        journey.current_location = request.POST.get('current_location') or ""
        journey.hotel_address = request.POST.get('hotel_address', '')
        journey.flight_number = ''
        journey.travel_insurance = 'travel_insurance' in request.POST
        journey.insurance_provider = request.POST.get('insurance_provider', '')
        journey.insurance_policy_number = request.POST.get('insurance_policy_number', '')
        journey.save()
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def add_contact(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'})
    
    try:
        name = request.POST.get('name', '').strip()
        relationship = request.POST.get('relationship', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()

        # Strict validations
        if not re.match(r"^[a-zA-Z\s]{3,}$", name):
            return JsonResponse({'success': False, 'error': 'Name must contain only alphabets and be at least 3 characters.'})

        if not re.match(r"^\d{10}$", phone):
            return JsonResponse({'success': False, 'error': 'Enter a valid 10-digit phone number.'})

        if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
            return JsonResponse({'success': False, 'error': 'Enter a valid email address.'})

        # Set all other contacts to non-primary if this is primary
        if request.POST.get('is_primary') == 'on':
            EmergencyContact.objects.filter(user=request.user).update(is_primary=False)
        
        contact = EmergencyContact.objects.create(
            user=request.user,
            name=name,
            relationship=relationship,
            phone=phone,
            email=email,
            is_primary=(request.POST.get('is_primary') == 'on'),
            whatsapp_enabled=(request.POST.get('whatsapp_enabled') == 'on'),
            sms_enabled=(request.POST.get('sms_enabled') == 'on')
        )
        
        return JsonResponse({'success': True, 'contact_id': contact.id})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_contact(request, contact_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'})
    
    try:
        contact = EmergencyContact.objects.get(id=contact_id, user=request.user)
        name = request.POST.get('name', '').strip()
        relationship = request.POST.get('relationship', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()

        # Strict validations
        if not re.match(r"^[a-zA-Z\s]{3,}$", name):
            return JsonResponse({'success': False, 'error': 'Name must contain only alphabets and be at least 3 characters.'})

        if not re.match(r"^\d{10}$", phone):
            return JsonResponse({'success': False, 'error': 'Enter a valid 10-digit phone number.'})

        if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
            return JsonResponse({'success': False, 'error': 'Enter a valid email address.'})
        
        # Set all other contacts to non-primary if this is primary
        if request.POST.get('is_primary') == 'on':
            EmergencyContact.objects.filter(user=request.user).exclude(id=contact_id).update(is_primary=False)
        
        contact.name = name
        contact.relationship = relationship
        contact.phone = phone
        contact.email = email
        contact.is_primary = (request.POST.get('is_primary') == 'on')
        contact.whatsapp_enabled = (request.POST.get('whatsapp_enabled') == 'on')
        contact.sms_enabled = (request.POST.get('sms_enabled') == 'on')
        contact.save()
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def get_contact(request, contact_id):
    try:
        contact = EmergencyContact.objects.get(id=contact_id, user=request.user)
        return JsonResponse({
            'id': contact.id,
            'name': contact.name,
            'relationship': contact.relationship,
            'phone': contact.phone,
            'email': contact.email,
            'is_primary': contact.is_primary,
            'whatsapp_enabled': contact.whatsapp_enabled,
            'sms_enabled': contact.sms_enabled
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def delete_contact(request, contact_id):
    if request.method != 'DELETE':
        return JsonResponse({'success': False, 'error': 'Invalid method'})
    
    try:
        contact = EmergencyContact.objects.get(id=contact_id, user=request.user)
        contact.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# 🌙 Worker Dashboard
def _get_or_create_worker_profile(user):
    profile, _ = WorkerProfile.objects.get_or_create(
        user=user,
        defaults={
            "employee_id": "",
            "company_name": "",
            "phone": user.phone or "",
        },
    )
    if not profile.phone and user.phone:
        profile.phone = user.phone
        profile.save(update_fields=["phone"])
    return profile


def _serialize_checkin(checkin):
    if not checkin:
        return None
    return {
        "id": checkin.id,
        "status": checkin.status,
        "status_label": checkin.get_status_display(),
        "timestamp": checkin.timestamp.isoformat(),
        "timestamp_label": _timestamp_label(checkin.timestamp),
        "latitude": checkin.location_lat,
        "longitude": checkin.location_lng,
        "location_label": _compose_location_label(checkin.location_lat, checkin.location_lng),
        "display_location": _compose_location_label(checkin.location_lat, checkin.location_lng, include_coordinates=True),
    }


def _serialize_shift(shift):
    if not shift:
        return None
    actual_start = shift.actual_start or shift.start_time
    actual_end = shift.actual_end
    active_end = actual_end or timezone.now()
    duration_minutes = None
    if actual_start:
        duration_minutes = max(int((active_end - actual_start).total_seconds() // 60), 0)
    return {
        "id": shift.id,
        "status": shift.status,
        "status_label": shift.get_status_display(),
        "company_name": shift.company_name or "",
        "scheduled_start": shift.start_time.isoformat() if shift.start_time else None,
        "scheduled_end": shift.end_time.isoformat() if shift.end_time else None,
        "actual_start": actual_start.isoformat() if actual_start else None,
        "actual_end": actual_end.isoformat() if actual_end else None,
        "duration_minutes": duration_minutes,
    }


def _normalize_worker_leave_dates(raw_dates):
    if raw_dates is None:
        return []

    if isinstance(raw_dates, str):
        raw_dates = raw_dates.strip()
        if not raw_dates:
            raw_dates = []
        else:
            try:
                parsed = json.loads(raw_dates)
                raw_dates = parsed if isinstance(parsed, list) else [raw_dates]
            except json.JSONDecodeError:
                raw_dates = [value.strip() for value in raw_dates.split(",") if value.strip()]

    if not isinstance(raw_dates, (list, tuple, set)):
        raw_dates = [raw_dates]

    normalized_dates = []
    for value in raw_dates:
        if value in (None, ""):
            continue
        try:
            normalized_dates.append(date.fromisoformat(str(value)[:10]).isoformat())
        except ValueError:
            continue
    return sorted(set(normalized_dates))


WORKER_SHIFT_AUTO_SOS_MINUTES = 15


def _worker_schedule_window(worker_profile, reference_dt=None):
    if not worker_profile or not worker_profile.usual_shift_start or not worker_profile.usual_shift_end:
        return None, None

    local_now = timezone.localtime(reference_dt or timezone.now())
    start_time = worker_profile.usual_shift_start
    end_time = worker_profile.usual_shift_end
    overnight = end_time <= start_time
    start_date = local_now.date()

    if overnight and local_now.time() < end_time:
        start_date = start_date - timedelta(days=1)

    end_date = start_date if end_time > start_time else start_date + timedelta(days=1)
    tz = timezone.get_current_timezone()
    scheduled_start = timezone.make_aware(datetime.combine(start_date, start_time), tz)
    scheduled_end = timezone.make_aware(datetime.combine(end_date, end_time), tz)
    return scheduled_start, scheduled_end


def _serialize_worker_shift_preferences(worker_profile, reference_dt=None):
    leave_dates = _normalize_worker_leave_dates(getattr(worker_profile, "leave_dates", []))
    scheduled_start, scheduled_end = _worker_schedule_window(worker_profile, reference_dt=reference_dt)
    return {
        "usual_shift_start": worker_profile.usual_shift_start.strftime("%H:%M") if getattr(worker_profile, "usual_shift_start", None) else "",
        "usual_shift_end": worker_profile.usual_shift_end.strftime("%H:%M") if getattr(worker_profile, "usual_shift_end", None) else "",
        "leave_dates": leave_dates,
        "leave_today": timezone.localdate(reference_dt or timezone.now()).isoformat() in leave_dates,
        "monitoring_enabled": bool(getattr(worker_profile, "usual_shift_start", None) and getattr(worker_profile, "usual_shift_end", None)),
        "auto_sos_after_minutes": WORKER_SHIFT_AUTO_SOS_MINUTES,
        "scheduled_start": scheduled_start.isoformat() if scheduled_start else None,
        "scheduled_end": scheduled_end.isoformat() if scheduled_end else None,
    }


def _find_worker_shift_for_schedule(user, scheduled_start, scheduled_end):
    if not scheduled_start or not scheduled_end:
        return None

    tolerance = timedelta(minutes=1)
    return (
        Shift.objects.filter(
            user=user,
            start_time__gte=scheduled_start - tolerance,
            start_time__lte=scheduled_start + tolerance,
            end_time__gte=scheduled_end - tolerance,
            end_time__lte=scheduled_end + tolerance,
        )
        .order_by("-actual_start", "-start_time")
        .first()
    )


def _normalized_contact_phone(value):
    normalized = re.sub(r"[^\d+]", "", str(value or "").strip())
    return normalized or ""


def _dispatch_contact_rows(user):
    contacts = []
    seen = set()

    for contact in EmergencyContact.objects.filter(user=user).order_by("-is_primary", "name"):
        phone = _normalized_contact_phone(contact.phone)
        email = (contact.email or "").strip().lower()
        dedupe_key = (phone, email, (contact.name or "").strip().lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        contacts.append(
            {
                "name": contact.name,
                "phone": contact.phone,
                "relationship": contact.get_relationship_display(),
                "is_primary": contact.is_primary,
                "sms_enabled": contact.sms_enabled,
                "whatsapp_enabled": contact.whatsapp_enabled,
                "email": contact.email or "",
                "source": "emergency-contact",
            }
        )

    if user.role == "worker":
        worker_profile = getattr(user, "worker_profile", None)
        if worker_profile and worker_profile.emergency_contact_name and worker_profile.emergency_contact_phone:
            employer_phone = _normalized_contact_phone(worker_profile.emergency_contact_phone)
            dedupe_key = (
                employer_phone,
                "",
                (worker_profile.emergency_contact_name or "").strip().lower(),
            )
            if employer_phone and dedupe_key not in seen:
                contacts.append(
                    {
                        "name": worker_profile.emergency_contact_name,
                        "phone": worker_profile.emergency_contact_phone,
                        "relationship": "Employer Contact",
                        "is_primary": not contacts,
                        "sms_enabled": True,
                        "whatsapp_enabled": True,
                        "email": "",
                        "source": "worker-profile",
                    }
                )
                seen.add(dedupe_key)

    return contacts


def _dispatch_shift_escalation_sos(user, scheduled_time, shift_type="check-in"):
    lat, lng = _resolve_user_coordinates(user)
    if lat is None or lng is None:
        return {
            "auto_sos_dispatched": False,
            "auto_sos_already_sent": False,
            "client_dispatch_required": True,
            "auto_sos_message": "Saved GPS location is unavailable, so SafePassage needs the open worker page to finish the SOS dispatch.",
            "delivery_channels": {"sms_contacts": 0, "whatsapp_contacts": 0, "total_channel_dispatches": 0},
        }

    existing_alert = (
        EmergencyAlert.objects.filter(
            user=user,
            mode="silent",
            timestamp__gte=scheduled_time - timedelta(minutes=WORKER_SHIFT_AUTO_SOS_MINUTES),
        )
        .order_by("-timestamp")
        .first()
    )
    if existing_alert:
        return {
            "auto_sos_dispatched": False,
            "auto_sos_already_sent": True,
            "client_dispatch_required": False,
            "auto_sos_message": "An automatic SOS has already been sent for this missed shift update.",
            "alert_id": existing_alert.id,
            "delivery_channels": {"sms_contacts": 0, "whatsapp_contacts": 0, "total_channel_dispatches": 0},
        }

    alert = EmergencyAlert.objects.create(user=user, latitude=lat, longitude=lng, mode="silent")

    from django.utils import timezone as tz
    display_name = (user.first_name or "").strip() or user.username
    time_str = tz.localtime(alert.timestamp).strftime("%d %B %Y, %I:%M %p")
    scheduled_str = tz.localtime(scheduled_time).strftime("%I:%M %p")
    action_label = "check-out" if shift_type == "check-out" else "check-in"

    subject = "🚨 SOS Alert Notification – Missed Shift " + action_label.title()
    message = (
        f"🚨 SOS Alert Notification\n\n"
        f"This is an automated alert from SafePassage.\n\n"
        f"A worker did not {action_label} within {WORKER_SHIFT_AUTO_SOS_MINUTES} minutes of their scheduled time. "
        f"An automatic SOS has been triggered.\n\n"
        f"----------------------------------------\n"
        f"Worker Information:\n"
        f"• Name: {display_name}\n"
        f"• Email: {user.email}\n\n"
        f"Shift Details:\n"
        f"• Expected {action_label.title()}: {scheduled_str}\n"
        f"• SOS Triggered At: {time_str}\n\n"
        f"Location:\n"
        f"• https://maps.google.com/?q={lat},{lng}\n\n"
        f"----------------------------------------\n"
        f"The worker may be in a potentially unsafe situation.\n\n"
        f"Kindly reach out to the worker or initiate appropriate emergency response.\n\n"
        f"This is an automated message generated by the SafePassage Safety System."
    )

    contacts = _dispatch_contact_rows(user)
    email_recipients = [c["email"] for c in contacts if c.get("email")]
    user_email = (user.email or "").strip()
    all_recipients = list(dict.fromkeys(([user_email] if user_email else []) + email_recipients))

    _send_broadcast_notifications(
        subject=subject,
        message_body=message,
        sender=_notification_sender_email(),
        recipient_list=all_recipients,
    )

    return {
        "auto_sos_dispatched": True,
        "auto_sos_already_sent": False,
        "client_dispatch_required": False,
        "auto_sos_message": f"Automatic SOS dispatched because the worker did not {action_label} within {WORKER_SHIFT_AUTO_SOS_MINUTES} minutes of the scheduled time.",
        "alert_id": alert.id,
        "delivery_channels": {"sms_contacts": 0, "whatsapp_contacts": 0, "total_channel_dispatches": 0},
    }


def _next_checkin_due_minutes(active_shift, last_checkin, interval_minutes=30):
    if not active_shift:
        return None
    reference_time = None
    if last_checkin:
        reference_time = last_checkin.timestamp
    elif active_shift.actual_start or active_shift.start_time:
        reference_time = active_shift.actual_start or active_shift.start_time
    if not reference_time:
        return None
    due_at = reference_time + timedelta(minutes=interval_minutes)
    return max(int((due_at - timezone.now()).total_seconds() // 60), 0)


def _build_worker_safe_havens_payload(lat, lng, radius_km=10):
    havens = _nearby_records(SafeHaven.objects.all(), lat, lng, radius_km=radius_km)
    return [
        {
            "id": haven.id,
            "name": haven.name,
            "type": haven.get_type_display(),
            "type_code": haven.type,
            "latitude": haven.latitude,
            "longitude": haven.longitude,
            "address": haven.address,
            "phone": haven.phone,
            "is_open_24_7": haven.is_open_24_7,
            "distance_km": distance_km,
        }
        for haven, distance_km in havens[:8]
    ]


def _build_worker_risk_payload(lat, lng):
    base_payload = _build_risk_payload(lat, lng)
    verified_havens = _build_worker_safe_havens_payload(lat, lng, radius_km=8)
    base_score = base_payload.get("risk_score")
    local_hour = timezone.localtime().hour
    is_night_window = local_hour >= 20 or local_hour < 6
    adjustments = []
    risk_adjustment = 0
    if is_night_window:
        risk_adjustment += 10
        adjustments.append("night commute window is active")
    if not verified_havens:
        risk_adjustment += 6
        adjustments.append("no verified safe haven is stored nearby")

    if base_score is None:
        adjusted_score = None
        adjusted_label = "UNAVAILABLE"
    else:
        adjusted_score = min(100, base_score + risk_adjustment)
        adjusted_label = _normalize_risk_label(adjusted_score)

    advice = base_payload.get("advice") or "No live night-worker safety signals are available for this area yet."
    if adjustments:
        advice = f"Night shift caution: {'; '.join(adjustments)}. {advice}"

    base_payload.update(
        {
            "base_risk_score": base_score,
            "risk_score": adjusted_score,
            "risk_label": adjusted_label,
            "advice": advice,
            "night_window": is_night_window,
            "night_adjustment": risk_adjustment,
            "nearby_safe_havens": verified_havens,
        }
    )
    return base_payload


def _build_worker_alerts(user, lat, lng, active_shift=None):
    alerts = _build_incident_alerts(lat, lng, limit=6)
    if active_shift:
        last_checkin = CheckIn.objects.filter(shift=active_shift).order_by("-timestamp").first()
        reference_time = last_checkin.timestamp if last_checkin else (active_shift.actual_start or active_shift.start_time)
        if reference_time:
            overdue_minutes = int((timezone.now() - reference_time).total_seconds() // 60) - 30
            if overdue_minutes > 0:
                alerts.insert(
                    0,
                    {
                        "id": f"shift-monitor-{active_shift.id}",
                        "incident_type": "checkin",
                        "title": "Safety check-in overdue",
                        "description": "Your active shift has passed the scheduled safety check-in window without a fresh confirmation.",
                        "severity": "HIGH" if overdue_minutes >= 15 else "MEDIUM",
                        "distance_km": None,
                        "latitude": lat,
                        "longitude": lng,
                        "source": "shift-monitor",
                        "created_at": timezone.now().isoformat(),
                    },
                )
    return alerts[:6]


def _build_worker_dashboard_payload(user, lat, lng):
    worker_profile = _get_or_create_worker_profile(user)
    active_shift = Shift.objects.filter(user=user, status="active").order_by("-actual_start", "-start_time").first()
    active_shift_checkin = CheckIn.objects.filter(shift=active_shift).order_by("-timestamp").first() if active_shift else None
    recent_checkins = list(CheckIn.objects.filter(user=user).order_by("-timestamp")[:6])
    worker_risk = _build_worker_risk_payload(lat, lng)
    alerts = _build_worker_alerts(user, lat, lng, active_shift=active_shift)

    return {
        "status": "success",
        "location": worker_risk.get("location") or _resolve_location_name(lat, lng),
        "coordinates": {
            "latitude": round(lat, 6),
            "longitude": round(lng, 6),
        },
        "worker": {
            "full_name": user.get_full_name() or user.first_name or user.username,
            "employee_id": worker_profile.employee_id or "",
            "company_name": worker_profile.company_name or "",
            "phone": worker_profile.phone or "",
            "email": user.email or "",
        },
        "shift_active": bool(active_shift),
        "active_shift": _serialize_shift(active_shift),
        "recent_checkins": [_serialize_checkin(checkin) for checkin in recent_checkins],
        "last_checkin": _serialize_checkin(recent_checkins[0]) if recent_checkins else None,
        "next_checkin_due_minutes": _next_checkin_due_minutes(active_shift, active_shift_checkin),
        "risk_score": worker_risk.get("risk_score"),
        "risk_label": worker_risk.get("risk_label"),
        "risk_advice": worker_risk.get("advice"),
        "risk_breakdown": worker_risk.get("breakdown"),
        "weather": worker_risk.get("weather"),
        "night_window": worker_risk.get("night_window"),
        "nearby_safe_havens": worker_risk.get("nearby_safe_havens"),
        "risk_hotspots": worker_risk.get("nearby_hotspots"),
        "alerts": alerts,
    }


def _build_worker_shift_payload(user):
    worker_profile = _get_or_create_worker_profile(user)
    active_shift = Shift.objects.filter(user=user, status="active").order_by("-actual_start", "-start_time").first()
    active_shift_checkin = CheckIn.objects.filter(shift=active_shift).order_by("-timestamp").first() if active_shift else None
    history = list(Shift.objects.filter(user=user).order_by("-start_time")[:10])
    recent_checkins = list(CheckIn.objects.filter(user=user).order_by("-timestamp")[:10])
    current_lat = active_shift_checkin.location_lat if active_shift_checkin and active_shift_checkin.location_lat is not None else None
    current_lng = active_shift_checkin.location_lng if active_shift_checkin and active_shift_checkin.location_lng is not None else None
    if current_lat is None or current_lng is None:
        current_lat, current_lng = _resolve_user_coordinates(user)
    return {
        "status": "success",
        "active_shift": _serialize_shift(active_shift),
        "recent_checkins": [_serialize_checkin(checkin) for checkin in recent_checkins],
        "shift_history": [_serialize_shift(shift) for shift in history],
        "next_checkin_due_minutes": _next_checkin_due_minutes(active_shift, active_shift_checkin),
        "current_location": _compose_location_label(current_lat, current_lng),
        "current_location_display": _compose_location_label(current_lat, current_lng, include_coordinates=True),
        "schedule_preferences": _serialize_worker_shift_preferences(worker_profile),
    }


def _resolve_worker_coordinates(request, source):
    lat, lng = _parse_coordinates(source)
    if lat is None or lng is None:
        lat, lng = _resolve_user_coordinates(request.user)
    return lat, lng


def _build_worker_template_context(user, **extra_context):
    current_lat, current_lng = _resolve_user_coordinates(user)
    worker_profile = _get_or_create_worker_profile(user)
    base_context = {
        "worker": worker_profile,
        "worker_last_lat": current_lat,
        "worker_last_lng": current_lng,
        "has_worker_fallback_location": current_lat is not None and current_lng is not None,
        "worker_location_label": _compose_location_label(current_lat, current_lng),
        "worker_location_display": _compose_location_label(current_lat, current_lng, include_coordinates=True),
        "current_worker_location": _compose_location_label(current_lat, current_lng),
        "current_worker_location_display": _compose_location_label(current_lat, current_lng, include_coordinates=True),
        "emergency_contacts": EmergencyContact.objects.filter(user=user).order_by("-is_primary", "name"),
        "emergency_contacts_count": EmergencyContact.objects.filter(user=user).count(),
        "worker_leave_dates": _normalize_worker_leave_dates(getattr(worker_profile, "leave_dates", [])),
    }
    base_context.update(extra_context)
    return base_context


@worker_required
def worker_dashboard(request):
    active_shift = Shift.objects.filter(user=request.user, status="active").order_by("-actual_start", "-start_time").first()
    return render(
        request,
        "worker_dashboard.html",
        _build_worker_template_context(request.user, active_shift=active_shift),
    )

@worker_required
def worker_route_planner(request):
    return render(request, "worker_route_planner.html", _build_worker_template_context(request.user))

@worker_required
def worker_safe_havens(request):
    return render(
        request,
        "worker_safe_havens.html",
        _build_worker_template_context(request.user, havens_count=SafeHaven.objects.count()),
    )

@worker_required
def worker_checkins(request):
    active_shift = Shift.objects.filter(user=request.user, status="active").order_by("-actual_start", "-start_time").first()
    history = Shift.objects.filter(user=request.user).order_by("-start_time")[:10]
    recent_checkins = CheckIn.objects.filter(user=request.user).order_by("-timestamp")[:10]
    current_lat, current_lng = _resolve_user_coordinates(request.user)
    return render(
        request,
        "worker_checkins.html",
        _build_worker_template_context(
            request.user,
            active_shift=active_shift,
            history=history,
            recent_checkins=recent_checkins,
            recent_checkins_payload=[_serialize_checkin(checkin) for checkin in recent_checkins],
            current_worker_location=_compose_location_label(current_lat, current_lng),
            current_worker_location_display=_compose_location_label(current_lat, current_lng, include_coordinates=True),
        ),
    )


@worker_required
def worker_risk_map(request):
    return render(request, "worker_risk_map.html", _build_worker_template_context(request.user))

@worker_required
def worker_emergency(request):
    latest_alert = EmergencyAlert.objects.filter(user=request.user).order_by("-timestamp").first()
    current_lat, current_lng = _resolve_user_coordinates(request.user)
    return render(
        request,
        "worker_emergency.html",
        _build_worker_template_context(
            request.user,
            latest_alert=latest_alert,
            latest_alert_location=_compose_location_label(latest_alert.latitude, latest_alert.longitude) if latest_alert else None,
            current_worker_location=_compose_location_label(current_lat, current_lng),
        ),
    )


@worker_required
def worker_alerts(request):
    return render(request, "worker_alerts.html", _build_worker_template_context(request.user))

@worker_required
def worker_profile(request):
    worker = _get_or_create_worker_profile(request.user)

    if request.method == "POST":
        first_name = (request.POST.get("first_name") or "").strip()
        last_name = (request.POST.get("last_name") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        employee_id = (request.POST.get("employee_id") or "").strip()
        company_name = (request.POST.get("company_name") or "").strip()
        designation = (request.POST.get("designation") or "").strip()
        department = (request.POST.get("department") or "").strip()
        work_location = (request.POST.get("work_location") or "").strip()
        home_address = (request.POST.get("home_address") or "").strip()
        emergency_contact_name = (request.POST.get("emergency_contact_name") or "").strip()
        emergency_contact_phone = (request.POST.get("emergency_contact_phone") or "").strip()
        blood_group = (request.POST.get("blood_group") or "").strip().upper()
        usual_shift_start = (request.POST.get("usual_shift_start") or "").strip()
        usual_shift_end = (request.POST.get("usual_shift_end") or "").strip()
        start_period = request.POST.get("shift_start_period", "AM")
        end_period = request.POST.get("shift_end_period", "AM")

        required_fields = {
            "First name": first_name,
            "Last name": last_name,
            "Phone": phone,
            "Employee ID": employee_id,
            "Company": company_name,
            "Designation": designation,
            "Department": department,
            "Work location": work_location,
            "Home address": home_address,
            "Blood group": blood_group,
            "Emergency contact name": emergency_contact_name,
            "Emergency contact phone": emergency_contact_phone,
            "Usual shift start": usual_shift_start,
            "Usual shift end": usual_shift_end,
        }
        missing_fields = [label for label, value in required_fields.items() if not value]
        if missing_fields:
            messages.error(request, f"Complete all required worker profile fields: {', '.join(missing_fields)}.")
            return redirect("worker_profile")

        if not re.match(r"^[a-zA-Z\s]{2,}$", first_name):
            messages.error(request, "First name must contain only alphabets and be at least 2 characters.")
            return redirect("worker_profile")

        if not re.match(r"^[a-zA-Z\s]{2,}$", last_name):
            messages.error(request, "Last name must contain only alphabets and be at least 2 characters.")
            return redirect("worker_profile")

        if not re.match(r"^\d{10}$", phone):
            messages.error(request, "Phone number must be a valid 10-digit mobile number.")
            return redirect("worker_profile")

        if not re.match(r"^[a-zA-Z\s]{2,}$", emergency_contact_name):
            messages.error(request, "Emergency contact name must contain only alphabets and be at least 2 characters.")
            return redirect("worker_profile")

        if not re.match(r"^\d{10}$", emergency_contact_phone):
            messages.error(request, "Emergency contact phone must be a valid 10-digit mobile number.")
            return redirect("worker_profile")

        if blood_group not in {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}:
            messages.error(request, "Select a valid blood group.")
            return redirect("worker_profile")

        parsed_shift_start = None
        parsed_shift_end = None
        try:
            from datetime import time
            h_s, m_s = map(int, usual_shift_start.split(':'))
            # If picker was 12h, adjust based on period. If 24h, normalize.
            if h_s < 12 and start_period == "PM": h_s += 12
            elif h_s == 12 and start_period == "AM": h_s = 0
            parsed_shift_start = time(h_s, m_s)

            h_e, m_e = map(int, usual_shift_end.split(':'))
            if h_e < 12 and end_period == "PM": h_e += 12
            elif h_e == 12 and end_period == "AM": h_e = 0
            parsed_shift_end = time(h_e, m_e)
        except (ValueError, IndexError):
            messages.error(request, "Invalid shift timing format.")
            return redirect("worker_profile")

        request.user.first_name = first_name
        request.user.last_name = last_name
        request.user.phone = phone
        if "profile_image" in request.FILES:
            request.user.profile_image = request.FILES["profile_image"]
        request.user.save(update_fields=["first_name", "last_name", "phone", "profile_image"] if "profile_image" in request.FILES else ["first_name", "last_name", "phone"])

        worker.employee_id = employee_id
        worker.company_name = company_name
        worker.phone = phone
        worker.designation = designation
        worker.department = department
        worker.work_location = work_location
        worker.home_address = home_address
        worker.emergency_contact_name = emergency_contact_name
        worker.emergency_contact_phone = emergency_contact_phone
        worker.blood_group = blood_group
        worker.usual_shift_start = parsed_shift_start
        worker.usual_shift_end = parsed_shift_end
        worker.save()

        messages.success(request, "Worker profile updated successfully.")
        return redirect("worker_profile")

    return render(request, "worker_profile.html", _build_worker_template_context(request.user))

# --- Worker API Endpoints ---

@login_required
def api_worker_sos_target(request):
    guard_response = _worker_api_guard(request)
    if guard_response:
        return guard_response

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=405)

    payload = _load_request_payload(request)
    contact_name = (payload.get("name") or "").strip()
    contact_phone = (payload.get("phone") or "").strip()

    if not re.match(r"^[a-zA-Z\s]{2,}$", contact_name):
        return JsonResponse(
            {"status": "error", "message": "Contact name must contain only alphabets and be at least 2 characters."},
            status=400,
        )

    if not re.match(r"^\d{10}$", contact_phone):
        return JsonResponse(
            {"status": "error", "message": "Phone number must be a valid 10-digit mobile number."},
            status=400,
        )

    worker_profile = _get_or_create_worker_profile(request.user)
    worker_profile.emergency_contact_name = contact_name
    worker_profile.emergency_contact_phone = contact_phone
    worker_profile.save(update_fields=["emergency_contact_name", "emergency_contact_phone"])

    return JsonResponse(
        {
            "status": "success",
            "message": "Worker SOS target updated successfully.",
            "contact": {
                "name": worker_profile.emergency_contact_name,
                "phone": worker_profile.emergency_contact_phone,
                "relationship": "Employer Contact",
                "channels": ["SMS", "WhatsApp"],
                "source": "worker-profile",
            },
        }
    )

@login_required
def api_worker_dashboard_data(request):
    guard_response = _worker_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _resolve_worker_coordinates(request, request.GET)
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Live coordinates are required."}, status=400)
    return JsonResponse(_build_worker_dashboard_payload(request.user, lat, lng))


@login_required
def api_worker_risk(request):
    guard_response = _worker_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _resolve_worker_coordinates(request, request.GET)
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Live coordinates are required."}, status=400)
    payload = _build_worker_risk_payload(lat, lng)
    payload["status"] = "success"
    return JsonResponse(payload)


@login_required
def api_worker_safe_havens(request):
    guard_response = _worker_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _resolve_worker_coordinates(request, request.GET)
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Live coordinates are required."}, status=400)
    havens = _build_worker_safe_havens_payload(lat, lng)
    return JsonResponse(
        {
            "status": "success",
            "location": _resolve_location_name(lat, lng),
            "coordinates": {"latitude": round(lat, 6), "longitude": round(lng, 6)},
            "count": len(havens),
            "havens": havens,
        }
    )


@login_required
def api_worker_alerts(request):
    guard_response = _worker_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _resolve_worker_coordinates(request, request.GET)
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Live coordinates are required."}, status=400)
    active_shift = Shift.objects.filter(user=request.user, status="active").order_by("-actual_start", "-start_time").first()
    alerts = _build_worker_alerts(request.user, lat, lng, active_shift=active_shift)
    return JsonResponse(
        {
            "status": "success",
            "location": _compose_location_label(lat, lng),
            "count": len(alerts),
            "alerts": alerts,
        }
    )


@login_required
def api_worker_shift_status(request):
    guard_response = _worker_api_guard(request)
    if guard_response:
        return guard_response
    
    # Check for automated SOS conditions (15-min missed check-in/out)
    auto_alert_msg = _get_worker_automated_sos_check(request.user)
    
    payload = _build_worker_shift_payload(request.user)
    if auto_alert_msg:
        payload["auto_alert"] = auto_alert_msg
        
    return JsonResponse(payload)


@csrf_exempt
@worker_required
def api_worker_shift_preferences(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=405)

    payload = _load_request_payload(request)
    start_value = (payload.get("usual_shift_start") or "").strip()
    end_value = (payload.get("usual_shift_end") or "").strip()
    leave_dates = _normalize_worker_leave_dates(payload.get("leave_dates") or [])

    if not start_value or not end_value:
        return JsonResponse(
            {"status": "error", "message": "Usual check-in and check-out timings are required."},
            status=400,
        )

    try:
        parsed_start = datetime.strptime(start_value, "%H:%M").time()
        parsed_end = datetime.strptime(end_value, "%H:%M").time()
    except ValueError:
        return JsonResponse(
            {"status": "error", "message": "Shift timings must use the HH:MM format."},
            status=400,
        )

    worker_profile = _get_or_create_worker_profile(request.user)
    worker_profile.usual_shift_start = parsed_start
    worker_profile.usual_shift_end = parsed_end
    worker_profile.leave_dates = leave_dates
    worker_profile.save(update_fields=["usual_shift_start", "usual_shift_end", "leave_dates"])

    return JsonResponse(
        {
            "status": "success",
            "message": "Usual shift timings and leave days updated successfully.",
            "schedule_preferences": _serialize_worker_shift_preferences(worker_profile),
        }
    )


@login_required
def _api_worker_shift_escalation_legacy(request):
    """
    Returns escalation state for the worker's current shift timing.
    Escalation levels:
      none       – within grace period
      reminder_1 – 10–20 min overdue (first reminder)
      reminder_2 – 20–30 min overdue (second reminder)
      sos        – >30 min overdue  (auto-trigger SOS)
    Checks both shift-start overdue (pending shift) and shift-end overdue (active shift).
    """
    guard_response = _worker_api_guard(request)
    if guard_response:
        return guard_response

    now = timezone.now()
    GRACE_MIN = 10      # grace period before first reminder
    REMINDER2_MIN = 20  # second reminder threshold
    SOS_MIN = 30        # SOS trigger threshold

    def _level_and_minutes(minutes_overdue):
        if minutes_overdue < GRACE_MIN:
            return "none", minutes_overdue
        if minutes_overdue < REMINDER2_MIN:
            return "reminder_1", minutes_overdue
        if minutes_overdue < SOS_MIN:
            return "reminder_2", minutes_overdue
        return "sos", minutes_overdue

    # Check for pending shift (scheduled but not started)
    pending_shift = (
        Shift.objects.filter(user=request.user, status="pending", actual_start__isnull=True, start_time__lt=now)
        .order_by("start_time")
        .first()
    )
    if pending_shift:
        minutes_overdue = int((now - pending_shift.start_time).total_seconds() / 60)
        level, mins = _level_and_minutes(minutes_overdue)
        if level != "none":
            return JsonResponse({
                "escalation_level": level,
                "type": "start",
                "minutes_overdue": mins,
                "shift_id": pending_shift.id,
                "scheduled_time": pending_shift.start_time.isoformat(),
                "message": f"Shift start overdue by {mins} minutes. Please start your shift or respond to confirm safety.",
            })

    # Check active shift for overdue end
    active_shift = (
        Shift.objects.filter(user=request.user, status="active", actual_end__isnull=True, end_time__lt=now)
        .order_by("-actual_start")
        .first()
    )
    if active_shift:
        minutes_overdue = int((now - active_shift.end_time).total_seconds() / 60)
        level, mins = _level_and_minutes(minutes_overdue)
        if level != "none":
            return JsonResponse({
                "escalation_level": level,
                "type": "end",
                "minutes_overdue": mins,
                "shift_id": active_shift.id,
                "scheduled_time": active_shift.end_time.isoformat(),
                "message": f"Shift end overdue by {mins} minutes. Please end your shift or respond to confirm safety.",
            })

    return JsonResponse({"escalation_level": "none", "type": None, "minutes_overdue": 0, "message": ""})


@login_required
def api_worker_shift_escalation(request):
    guard_response = _worker_api_guard(request)
    if guard_response:
        return guard_response

    now = timezone.now()
    worker_profile = _get_or_create_worker_profile(request.user)
    schedule_preferences = _serialize_worker_shift_preferences(worker_profile, reference_dt=now)
    auto_sos_after_minutes = WORKER_SHIFT_AUTO_SOS_MINUTES

    if not schedule_preferences["monitoring_enabled"]:
        return JsonResponse(
            {
                "escalation_level": "none",
                "type": None,
                "minutes_overdue": 0,
                "message": "Set your usual check-in and check-out timings to enable automatic shift SOS monitoring.",
                "schedule_preferences": schedule_preferences,
                "auto_sos_after_minutes": auto_sos_after_minutes,
                "client_dispatch_required": False,
                "auto_sos_dispatched": False,
            }
        )

    scheduled_start, scheduled_end = _worker_schedule_window(worker_profile, reference_dt=now)
    if not scheduled_start or not scheduled_end:
        return JsonResponse(
            {
                "escalation_level": "none",
                "type": None,
                "minutes_overdue": 0,
                "message": "",
                "schedule_preferences": schedule_preferences,
                "auto_sos_after_minutes": auto_sos_after_minutes,
                "client_dispatch_required": False,
                "auto_sos_dispatched": False,
            }
        )

    scheduled_leave_day = scheduled_start.date().isoformat() in schedule_preferences["leave_dates"]
    active_shift = (
        Shift.objects.filter(user=request.user, status="active", actual_end__isnull=True)
        .order_by("-actual_start", "-start_time")
        .first()
    )
    scheduled_shift = _find_worker_shift_for_schedule(request.user, scheduled_start, scheduled_end)

    if active_shift and active_shift.end_time and now >= active_shift.end_time + timedelta(minutes=auto_sos_after_minutes):
        minutes_overdue = max(int((now - active_shift.end_time).total_seconds() // 60), auto_sos_after_minutes)
        response_payload = {
            "escalation_level": "sos",
            "type": "end",
            "minutes_overdue": minutes_overdue,
            "shift_id": active_shift.id,
            "scheduled_time": active_shift.end_time.isoformat(),
            "message": f"Shift check-out is overdue by {minutes_overdue} minutes. SafePassage auto-SOS activates {auto_sos_after_minutes} minutes after the scheduled end time if check-out is missed.",
            "schedule_preferences": schedule_preferences,
            "auto_sos_after_minutes": auto_sos_after_minutes,
        }
        response_payload.update(_dispatch_shift_escalation_sos(request.user, active_shift.end_time, shift_type="check-out"))
        return JsonResponse(response_payload)

    if scheduled_leave_day and not active_shift:
        return JsonResponse(
            {
                "escalation_level": "leave",
                "type": "leave",
                "minutes_overdue": 0,
                "message": f"{scheduled_start.date().isoformat()} is marked as a leave day. Automatic shift SOS monitoring is paused for this schedule.",
                "schedule_preferences": schedule_preferences,
                "auto_sos_after_minutes": auto_sos_after_minutes,
                "client_dispatch_required": False,
                "auto_sos_dispatched": False,
            }
        )

    if not scheduled_shift and now >= scheduled_start + timedelta(minutes=auto_sos_after_minutes):
        minutes_overdue = max(int((now - scheduled_start).total_seconds() // 60), auto_sos_after_minutes)
        response_payload = {
            "escalation_level": "sos",
            "type": "start",
            "minutes_overdue": minutes_overdue,
            "shift_id": None,
            "scheduled_time": scheduled_start.isoformat(),
            "message": f"Shift check-in is overdue by {minutes_overdue} minutes. SafePassage auto-SOS activates {auto_sos_after_minutes} minutes after the scheduled start time if check-in is missed.",
            "schedule_preferences": schedule_preferences,
            "auto_sos_after_minutes": auto_sos_after_minutes,
        }
        response_payload.update(_dispatch_shift_escalation_sos(request.user, scheduled_start, shift_type="check-in"))
        return JsonResponse(response_payload)

    return JsonResponse(
        {
            "escalation_level": "none",
            "type": None,
            "minutes_overdue": 0,
            "message": "",
            "schedule_preferences": schedule_preferences,
            "auto_sos_after_minutes": auto_sos_after_minutes,
            "client_dispatch_required": False,
            "auto_sos_dispatched": False,
        }
    )


@login_required
def api_worker_place_search(request):
    guard_response = _worker_api_guard(request)
    if guard_response:
        return guard_response

    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return JsonResponse(
            {
                "status": "error",
                "message": "Search query must be at least 2 characters.",
                "results": [],
            },
            status=400,
        )
    return JsonResponse({"status": "success", "query": query, "results": _search_india_places(query, limit=10)})


@csrf_exempt
@worker_required
def start_shift(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=405)

    payload = _load_request_payload(request)
    worker_profile = _get_or_create_worker_profile(request.user)
    company_name = (payload.get("company_name") or worker_profile.company_name or "").strip()
    now = timezone.now()
    scheduled_start, scheduled_end = _worker_schedule_window(worker_profile, reference_dt=now)

    Shift.objects.filter(user=request.user, status="active").update(status="completed", actual_end=now)

    shift = Shift.objects.create(
        user=request.user,
        start_time=scheduled_start or now,
        end_time=scheduled_end or (now + timedelta(hours=8)),
        status="active",
        actual_start=now,
        company_name=company_name,
    )
    lat, lng = _parse_coordinates({"lat": payload.get("lat"), "lng": payload.get("lng")})
    if lat is None or lng is None:
        lat, lng = _resolve_user_coordinates(request.user)
    initial_checkin = None
    if lat is not None and lng is not None:
        UserLocation.objects.update_or_create(
            user=request.user,
            defaults={"latitude": lat, "longitude": lng},
        )
        initial_checkin = CheckIn.objects.create(
            user=request.user,
            shift=shift,
            status="ok",
            location_lat=lat,
            location_lng=lng,
        )
    return JsonResponse(
        {
            "status": "success",
            "shift": _serialize_shift(shift),
            "initial_checkin": _serialize_checkin(initial_checkin),
        }
    )

@csrf_exempt
@worker_required
def end_shift(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=405)

    active_shift = Shift.objects.filter(user=request.user, status="active").order_by("-actual_start", "-start_time").first()
    if not active_shift:
        return JsonResponse({"status": "error", "message": "No active shift."}, status=400)

    active_shift.status = "completed"
    active_shift.actual_end = timezone.now()
    active_shift.save(update_fields=["status", "actual_end"])
    return JsonResponse({"status": "success", "shift": _serialize_shift(active_shift)})

@csrf_exempt
@worker_required
def submit_checkin(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=405)

    payload = _load_request_payload(request)
    status = (payload.get("status") or "ok").lower()
    if status not in {"ok", "missed", "assistance", "checkout"}:
        status = "ok"

    active_shift = Shift.objects.filter(user=request.user, status="active").order_by("-actual_start", "-start_time").first()
    if not active_shift:
        return JsonResponse({"status": "error", "message": "No active shift."}, status=400)

    lat, lng = _parse_coordinates({"lat": payload.get("lat"), "lng": payload.get("lng")})
    if lat is not None and lng is not None:
        UserLocation.objects.update_or_create(
            user=request.user,
            defaults={"latitude": lat, "longitude": lng},
        )
    checkin = CheckIn.objects.create(
        user=request.user,
        shift=active_shift,
        status=status,
        location_lat=lat,
        location_lng=lng,
    )
    if status == "checkout":
        active_shift.status = "completed"
        active_shift.actual_end = timezone.now()
        active_shift.save(update_fields=["status", "actual_end"])
    next_checkin_due = None if status == "checkout" else _next_checkin_due_minutes(active_shift, checkin)
    
    # Auto SOS checks can be run on each check-in context as a safety-watch
    return JsonResponse(
        {
            "status": "success",
            "checkin": _serialize_checkin(checkin),
            "next_checkin_due_minutes": next_checkin_due,
        }
    )

def _get_worker_automated_sos_check(user):
    """
    Checks if a worker has missed their 15-minute start/end window.
    """
    if user.role != "worker" or not hasattr(user, 'worker_profile'):
        return None

    profile = user.worker_profile
    if not profile.usual_shift_start or not profile.usual_shift_end:
        return None

    from datetime import datetime
    now = timezone.localtime(timezone.now())
    now_time = now.time()

    def min_diff(t1, t2):
        d1 = datetime.combine(datetime.today(), t1)
        d2 = datetime.combine(datetime.today(), t2)
        return (d1 - d2).total_seconds() / 60

    active_shift = Shift.objects.filter(user=user, status="active").first()

    # If no shift but 15+ mins past start
    if not active_shift:
        diff = min_diff(now_time, profile.usual_shift_start)
        if 15 <= diff <= 120:
             if not EmergencyAlert.objects.filter(user=user, timestamp__gte=now - timedelta(minutes=60)).exists():
                   _dispatch_emergency_alert(user, None, None, "silent")
                   return "Automated Start Shift SOS Sent"
    
    # If shift active but 15+ mins past end
    elif active_shift:
        diff = min_diff(now_time, profile.usual_shift_end)
        if 15 <= diff <= 120:
             if not EmergencyAlert.objects.filter(user=user, timestamp__gte=now - timedelta(minutes=60)).exists():
                   _dispatch_emergency_alert(user, None, None, "silent")
                   return "Automated End Shift SOS Sent"
    return None

@worker_required
def get_safe_route(request):
    payload = _load_request_payload(request) if request.method == "POST" else request.GET
    destination_label = (
        payload.get("destination_label")
        or payload.get("destination_name")
        or payload.get("place_name")
    )

    source_lat, source_lng = _parse_coordinates(
        {
            "lat": payload.get("source_lat") or payload.get("sourceLatitude") or payload.get("lat"),
            "lng": payload.get("source_lng") or payload.get("sourceLongitude") or payload.get("lng"),
        }
    )
    if source_lat is None or source_lng is None:
        source_lat, source_lng = _resolve_user_coordinates(request.user)

    dest_lat, dest_lng = _parse_coordinates(
        {
            "lat": payload.get("dest_lat") or payload.get("destination_lat") or payload.get("destinationLatitude"),
            "lng": payload.get("dest_lng") or payload.get("destination_lng") or payload.get("destinationLongitude"),
        }
    )
    if dest_lat is None or dest_lng is None:
        destination_place = _resolve_route_destination(
            payload.get("destination_place")
            or payload.get("destination_place_id")
            or payload.get("destination_place_name")
            or destination_label
        )
        if not destination_place and destination_label:
            live_matches = _search_india_places(destination_label, limit=1)
            if live_matches:
                destination_place = live_matches[0]
        if destination_place:
            dest_lat = destination_place["latitude"]
            dest_lng = destination_place["longitude"]
            destination_label = destination_place["name"]

    if source_lat is None or source_lng is None:
        return JsonResponse({"status": "error", "message": "Source coordinates are required."}, status=400)
    if dest_lat is None or dest_lng is None:
        return JsonResponse({"status": "error", "message": "Choose a destination place or destination coordinates."}, status=400)

    return JsonResponse(
        _build_safe_route_payload(
            request.user,
            source_lat,
            source_lng,
            dest_lat,
            dest_lng,
            destination_label=destination_label,
        )
    )

# 🏢 Employer Dashboard
@login_required(login_url='login')
def employer_dashboard(request):
    if request.user.role != 'employer' and not request.user.is_superuser:
        return render(request, "unauthorized.html")
    return render(request, "employer_dashboard.html")

# 🛸 Risk Prediction Tool (Phase 2 legacy form)
def risk_form(request):
    if request.method == "POST":
        location = request.POST.get("location")
        year = float(request.POST.get("year", 2024))
        crime_value = float(request.POST.get("crime_value", 0))

        features = [year, crime_value]
        risk_label, risk_score = predict_risk(features)

        RiskPrediction.objects.create(
            location=location,
            year=year,
            crime_value=crime_value,
            predicted_risk=risk_label,
            risk_score=risk_score
        )

        return render(request, "result.html", {
            "location": location,
            "year": year,
            "crime_value": crime_value,
            "risk_label": risk_label,
            "risk_score": risk_score
        })

    return render(request, "form.html")

@login_required
def update_location(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            UserLocation.objects.update_or_create(
                user=request.user,
                defaults={
                    "latitude": data["latitude"],
                    "longitude": data["longitude"]
                }
            )
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
    return JsonResponse({"status": "invalid method"}, status=405)

def legal(request):
    return render(request, "legal.html")

def contact(request):
    return render(request, "legal.html") # Reusing for now

# API endpoint (called from frontend)
def get_risk_zones(request):
    lat, lng = _parse_coordinates(request.GET)
    if lat is None or lng is None:
        return JsonResponse({"error": "Invalid coordinates"}, status=400)

    payload = _build_risk_payload(lat, lng)
    return JsonResponse(
        {
            "zones": [
                {
                    "latitude": lat,
                    "longitude": lng,
                    "location": payload["location"],
                    "risk_score": payload["risk_score"],
                    "risk_label": payload["risk_label"],
                    "risk_type": "Live Contextual Risk",
                    "description": payload["advice"],
                }
            ]
        }
    )

@login_required
def trigger_sos(request):
    if request.method == "POST":
        try:
            data = _load_request_payload(request)
            lat, lng = _parse_coordinates(data)
            mode = (data.get("mode") or "silent").lower()
            if lat is None or lng is None:
                return JsonResponse({"status": "error", "message": "Invalid coordinates."}, status=400)
            if mode not in {"silent", "loud"}:
                return JsonResponse({"status": "error", "message": "Unsupported emergency mode."}, status=400)

            return JsonResponse(_dispatch_emergency_alert(request.user, lat, lng, mode))
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
    
    return JsonResponse({"status": "invalid method"}, status=405)

@login_required
def api_sos_history(request):
    """Return the last 20 SOS alerts triggered by the current user."""
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response
    contact_qs = EmergencyContact.objects.filter(user=request.user)
    sms_contacts = contact_qs.filter(sms_enabled=True).count()
    whatsapp_contacts = contact_qs.filter(whatsapp_enabled=True).count()
    alerts = EmergencyAlert.objects.filter(user=request.user).order_by('-timestamp')[:20]
    return JsonResponse({
        "alerts": [
            {
                "id": a.id,
                "mode": a.mode,
                "lat": a.latitude,
                "lng": a.longitude,
                "ts": a.timestamp.isoformat(),
                "status": a.status,
                "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                "acknowledged_by": a.acknowledged_by,
                "location_label": _compose_location_label(a.latitude, a.longitude, include_coordinates=True),
                "delivery_channels": {
                    "sms_contacts": sms_contacts,
                    "whatsapp_contacts": whatsapp_contacts,
                },
                "source": "app",
            }
            for a in alerts
        ]
    })

@tourist_required
def tourist_cultural_guide(request):
    return render(
        request,
        'tourist/cultural_guide.html',
        {
            'current_language': _normalize_language_code(translation.get_language(), default='en'),
        },
    )

@login_required
def get_cultural_data(request):
    guard_response = _tourist_api_guard(request)
    if guard_response:
        return guard_response

    lat, lng = _parse_coordinates(request.GET)
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Latitude and longitude are required."}, status=400)

    language = request.GET.get("language") or translation.get_language() or "en"
    assist_language = request.GET.get("assist_language") or request.GET.get("target_language") or "hi"
    return JsonResponse(_build_cultural_safety_payload(request.user, lat, lng, language, assist_language))

# 🛡️ Admin Dashboard
@login_required(login_url='login')
def admin_dashboard(request):
    # Only allow admin users
    if request.user.role != 'admin' and not request.user.is_superuser:
        return redirect("login")
    
    # Get admin statistics
    total_users = SafePassageUser.objects.count()
    total_tourists = SafePassageUser.objects.filter(role='tourist').count()
    total_workers = SafePassageUser.objects.filter(role='worker').count()
    total_employers = SafePassageUser.objects.filter(role='employer').count()
    total_admins = SafePassageUser.objects.filter(role='admin').count()
    
    # Recent user registrations
    recent_users = SafePassageUser.objects.order_by('-created_at')[:10]
    
    # Kerala specific data
    total_havens = SafeHaven.objects.count()
    total_crimes = CrimeRecord.objects.count()
    active_shifts = Shift.objects.filter(status='active').count()
    
    context = {
        'total_users': total_users,
        'total_tourists': total_tourists,
        'total_workers': total_workers,
        'total_employers': total_employers,
        'total_admins': total_admins,
        'recent_users': recent_users,
        'total_havens': total_havens,
        'total_crimes': total_crimes,
        'active_shifts': active_shifts,
    }
    
    return render(request, "admin_dashboard.html", context)


def _admin_status_title(value, default="Active"):
    normalized = (value or default).strip().lower()
    mapping = {
        "active": "Active",
        "in progress": "In Progress",
        "in-progress": "In Progress",
        "resolved": "Resolved",
        "reported": "Reported",
        "reviewing": "Reviewing",
        "completed": "Completed",
        "pending": "Pending",
        "idle": "Idle",
        "tracked": "Tracked",
        "on shift": "On Shift",
        "on-shift": "On Shift",
        "suspended": "Suspended",
        "ok": "OK",
        "missed": "Missed",
        "assistance": "Need Assistance",
    }
    return mapping.get(normalized, normalized.title() if normalized else default)


def _admin_status_slug(value, default="unknown"):
    normalized = _normalize_lookup_text(value)
    return normalized.replace(" ", "-") if normalized else default


def _timestamp_label(value):
    if not value:
        return "Unavailable"
    localized = timezone.localtime(value) if timezone.is_aware(value) else value
    return localized.strftime("%b %d, %Y %I:%M %p")


def _time_since_label(value):
    if not value:
        return "No recent activity"

    delta = timezone.now() - value
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "Just now"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"

    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"

    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def _coordinate_label(lat, lng):
    if lat is None or lng is None:
        return "No live location yet"
    return f"Lat {lat:.5f}, Lng {lng:.5f}"


def _latest_user_location_map():
    latest = {}
    for location in UserLocation.objects.select_related("user").order_by("user_id", "-timestamp"):
        if location.user_id not in latest:
            latest[location.user_id] = location
    return latest


def _active_shift_map():
    active = {}
    for shift in Shift.objects.select_related("user").filter(status="active").order_by("user_id", "-actual_start", "-start_time"):
        if shift.user_id not in active:
            active[shift.user_id] = shift
    return active


def _latest_user_alert_map():
    latest = {}
    for alert in EmergencyAlert.objects.select_related("user").order_by("user_id", "-timestamp"):
        if alert.user_id not in latest:
            latest[alert.user_id] = alert
    return latest


def _serialize_admin_risk_zone(zone):
    risk_label = _optional_risk_label(zone.risk_score)
    return {
        "id": zone.id,
        "city": zone.city or _coordinate_label(zone.latitude, zone.longitude),
        "risk_type": zone.get_risk_type_display(),
        "risk_score": zone.risk_score,
        "risk_label": risk_label,
        "risk_label_slug": _admin_status_slug(risk_label),
        "description": zone.description,
        "coordinates": _coordinate_label(zone.latitude, zone.longitude),
    }


def _build_recent_admin_alerts(limit=12):
    feed = []
    for alert in EmergencyAlert.objects.select_related("user").order_by("-timestamp")[:limit]:
        status = _admin_status_title(alert.status)
        severity = "HIGH" if alert.mode == "loud" else "MEDIUM"
        feed.append(
            {
                "kind": "SOS Alert",
                "kind_slug": "sos",
                "headline": f"{alert.user.get_role_display()} {alert.get_mode_display()} SOS",
                "detail": f"{alert.user.username} shared {_coordinate_label(alert.latitude, alert.longitude)}.",
                "status": status,
                "status_slug": _admin_status_slug(status),
                "severity": severity,
                "severity_slug": _admin_status_slug(severity),
                "timestamp": _timestamp_label(alert.timestamp),
                "timestamp_relative": _time_since_label(alert.timestamp),
                "_sort": alert.timestamp,
            }
        )

    for report in IncidentReport.objects.select_related("user").order_by("-created_at")[:limit]:
        status = _admin_status_title(report.status, "Reported")
        severity = _optional_risk_label(report.risk_score_snapshot)
        feed.append(
            {
                "kind": "Incident",
                "kind_slug": "incident",
                "headline": f"{report.get_incident_type_display()} reported by {report.user.username}",
                "detail": report.location_label or _coordinate_label(report.latitude, report.longitude),
                "status": status,
                "status_slug": _admin_status_slug(status),
                "severity": severity,
                "severity_slug": _admin_status_slug(severity),
                "timestamp": _timestamp_label(report.created_at),
                "timestamp_relative": _time_since_label(report.created_at),
                "_sort": report.created_at,
            }
        )

    feed.sort(key=lambda item: item["_sort"], reverse=True)
    trimmed_feed = feed[:limit]
    for item in trimmed_feed:
        item.pop("_sort", None)
    return trimmed_feed


def _build_admin_users_payload(role_filter=None, state_filter=None):
    now = timezone.now()
    users = list(SafePassageUser.objects.order_by("-created_at"))
    location_map = _latest_user_location_map()
    shift_map = _active_shift_map()
    alert_map = _latest_user_alert_map()

    role_counts = {
        "tourist": SafePassageUser.objects.filter(role="tourist").count(),
        "worker": SafePassageUser.objects.filter(role="worker").count(),
        "employer": SafePassageUser.objects.filter(role="employer").count(),
        "admin": SafePassageUser.objects.filter(role="admin").count(),
    }

    tracked_users = 0
    suspended_users = 0
    shift_users = 0
    rows = []
    for user in users:
        latest_location = location_map.get(user.id)
        active_shift = shift_map.get(user.id)
        latest_alert = alert_map.get(user.id)
        last_seen = latest_location.timestamp if latest_location else None
        is_tracked = bool(last_seen and last_seen >= now - timedelta(minutes=20))

        if is_tracked:
            tracked_users += 1
        if not user.is_active:
            suspended_users += 1
        if active_shift:
            shift_users += 1

        activity_state = (
            "Suspended"
            if not user.is_active
            else "On Shift"
            if active_shift
            else "Tracked"
            if is_tracked
            else "Idle"
        )

        if role_filter and user.role != role_filter:
            continue
        if state_filter == "tracked" and not is_tracked:
            continue
        if state_filter == "on-shift" and not active_shift:
            continue
        if state_filter == "suspended" and user.is_active:
            continue

        rows.append(
            {
                "id": user.id,
                "display_name": user.get_full_name().strip() or user.username,
                "username": user.username,
                "email": user.email,
                "phone": user.phone or "--",
                "role": user.role,
                "role_display": user.get_role_display(),
                "is_active": user.is_active,
                "created_at": _timestamp_label(user.created_at),
                "last_seen": _timestamp_label(last_seen) if last_seen else "No live location",
                "last_seen_relative": _time_since_label(last_seen),
                "location_label": _coordinate_label(
                    latest_location.latitude if latest_location else None,
                    latest_location.longitude if latest_location else None,
                ),
                "activity_state": activity_state,
                "activity_state_slug": _admin_status_slug(activity_state),
                "active_shift": bool(active_shift),
                "shift_window": (
                    f"{_timestamp_label(active_shift.actual_start or active_shift.start_time)} to {_timestamp_label(active_shift.end_time)}"
                    if active_shift
                    else "No active shift"
                ),
                "last_alert": _timestamp_label(latest_alert.timestamp) if latest_alert else "No SOS history",
            }
        )

    return {
        "summary": {
            "total_users": len(users),
            "active_accounts": sum(1 for user in users if user.is_active),
            "tracked_users": tracked_users,
            "suspended_users": suspended_users,
            "users_on_shift": shift_users,
            "roles": role_counts,
            "filtered_count": len(rows),
        },
        "filters": {
            "role": role_filter or "",
            "state": state_filter or "",
        },
        "users": rows,
    }


def _build_admin_live_risk_snapshot(lat, lng):
    nearby_zones = _nearby_records(RiskZone.objects.all(), lat, lng, radius_km=6)
    nearby_reports = _nearby_records(
        IncidentReport.objects.filter(created_at__gte=timezone.now() - timedelta(days=7)),
        lat,
        lng,
        radius_km=6,
    )
    nearby_crimes = _nearby_records(
        CrimeRecord.objects.filter(time__gte=timezone.now() - timedelta(days=14)),
        lat,
        lng,
        radius_km=6,
    )

    zone_signal = int(sum(zone.risk_score for zone, _ in nearby_zones) / len(nearby_zones)) if nearby_zones else None
    report_signal = min(100, len(nearby_reports) * 20) if nearby_reports else None
    crime_signal = min(100, len(nearby_crimes) * 15) if nearby_crimes else None

    weighted_components = []
    if zone_signal is not None:
        weighted_components.append((zone_signal, 0.6))
    if report_signal is not None:
        weighted_components.append((report_signal, 0.25))
    if crime_signal is not None:
        weighted_components.append((crime_signal, 0.15))

    if weighted_components:
        total_weight = sum(weight for _, weight in weighted_components)
        risk_score = int(round(sum(value * weight for value, weight in weighted_components) / total_weight))
        risk_label = _normalize_risk_label(risk_score)
    else:
        risk_score = None
        risk_label = "UNAVAILABLE"

    return {
        "risk_score": risk_score,
        "risk_label": risk_label,
        "risk_label_slug": _admin_status_slug(risk_label),
        "zone_count": len(nearby_zones),
        "incident_count": len(nearby_reports),
        "crime_count": len(nearby_crimes),
    }


def _build_admin_dashboard_payload():
    users_payload = _build_admin_users_payload()
    recent_alerts = _build_recent_admin_alerts(limit=8)
    open_incidents = IncidentReport.objects.exclude(status="resolved").count()
    total_sos = EmergencyAlert.objects.count()
    active_sos = EmergencyAlert.objects.exclude(status__iexact="resolved").count()
    high_risk_zones = RiskZone.objects.filter(risk_score__gte=75).count()
    active_tracking = users_payload["summary"]["tracked_users"]
    top_zones = [_serialize_admin_risk_zone(zone) for zone in RiskZone.objects.order_by("-risk_score", "city")[:6]]
    tracked_users = [
        user_row
        for user_row in users_payload["users"]
        if user_row["activity_state"] in {"Tracked", "On Shift"}
    ][:6]

    return {
        "summary": {
            "total_users": users_payload["summary"]["total_users"],
            "active_tracking": active_tracking,
            "total_sos": total_sos,
            "active_sos": active_sos,
            "high_risk_zones": high_risk_zones,
            "open_incidents": open_incidents,
            "safe_havens": SafeHaven.objects.count(),
            "active_shifts": Shift.objects.filter(status="active").count(),
            "crime_records": CrimeRecord.objects.count(),
            "risk_predictions": RiskPrediction.objects.count(),
            "tourists": users_payload["summary"]["roles"]["tourist"],
            "workers": users_payload["summary"]["roles"]["worker"],
        },
        "recent_alerts": recent_alerts,
        "top_zones": top_zones,
        "tracked_users": tracked_users,
        "recent_users": users_payload["users"][:8],
    }


def _build_admin_risk_monitor_payload():
    now = timezone.now()
    latest_locations = _latest_user_location_map()
    active_shifts = _active_shift_map()
    live_monitors = []
    for user_id, location in latest_locations.items():
        if location.timestamp < now - timedelta(minutes=30):
            continue
        user = location.user
        if not user.is_active:
            continue
        risk_snapshot = _build_admin_live_risk_snapshot(location.latitude, location.longitude)
        live_monitors.append(
            {
                "user_id": user.id,
                "display_name": user.get_full_name().strip() or user.username,
                "username": user.username,
                "role_display": user.get_role_display(),
                "location_label": _coordinate_label(location.latitude, location.longitude),
                "last_seen": _timestamp_label(location.timestamp),
                "last_seen_relative": _time_since_label(location.timestamp),
                "risk_score": risk_snapshot["risk_score"],
                "risk_label": risk_snapshot["risk_label"],
                "risk_label_slug": risk_snapshot["risk_label_slug"],
                "zone_count": risk_snapshot["zone_count"],
                "incident_count": risk_snapshot["incident_count"],
                "crime_count": risk_snapshot["crime_count"],
                "active_shift": bool(active_shifts.get(user_id)),
            }
        )

    live_monitors.sort(
        key=lambda item: (
            item["risk_score"] if item["risk_score"] is not None else -1,
            item["incident_count"],
            item["crime_count"],
        ),
        reverse=True,
    )

    recent_crimes = [
        {
            "area_name": record.area_name,
            "crime_type": record.crime_type,
            "time": _timestamp_label(record.time),
            "coordinates": _coordinate_label(record.latitude, record.longitude),
        }
        for record in CrimeRecord.objects.order_by("-time")[:10]
    ]

    risk_zones = [_serialize_admin_risk_zone(zone) for zone in RiskZone.objects.order_by("-risk_score", "city")[:12]]

    return {
        "summary": {
            "tracked_locations": len(live_monitors),
            "high_risk_zones": RiskZone.objects.filter(risk_score__gte=75).count(),
            "medium_risk_zones": RiskZone.objects.filter(risk_score__gte=45, risk_score__lt=75).count(),
            "recent_crime_records": len(recent_crimes),
        },
        "live_monitors": live_monitors,
        "risk_zones": risk_zones,
        "recent_crimes": recent_crimes,
    }


def _build_admin_sos_payload(status_filter=None):
    alerts = list(EmergencyAlert.objects.select_related("user").order_by("-timestamp"))
    rows = []
    counts = {"total": len(alerts), "active": 0, "in_progress": 0, "resolved": 0}
    for alert in alerts:
        status = _admin_status_title(alert.status)
        if status == "Resolved":
            counts["resolved"] += 1
        elif status == "In Progress":
            counts["in_progress"] += 1
        else:
            counts["active"] += 1

        if status_filter and _admin_status_slug(status) != status_filter:
            continue

        rows.append(
            {
                "id": alert.id,
                "user": alert.user.get_full_name().strip() or alert.user.username,
                "username": alert.user.username,
                "role_display": alert.user.get_role_display(),
                "mode": alert.get_mode_display(),
                "status": status,
                "status_slug": _admin_status_slug(status),
                "location_label": _coordinate_label(alert.latitude, alert.longitude),
                "timestamp": _timestamp_label(alert.timestamp),
                "timestamp_relative": _time_since_label(alert.timestamp),
            }
        )

    return {"summary": counts, "status_filter": status_filter or "", "alerts": rows[:40]}


def _build_admin_incidents_payload(status_filter=None):
    reports = list(IncidentReport.objects.select_related("user").order_by("-created_at"))
    rows = []
    counts = {"total": len(reports), "reported": 0, "reviewing": 0, "resolved": 0}
    for report in reports:
        status = _admin_status_title(report.status, "Reported")
        if status == "Resolved":
            counts["resolved"] += 1
        elif status == "Reviewing":
            counts["reviewing"] += 1
        else:
            counts["reported"] += 1

        if status_filter and _admin_status_slug(status) != status_filter:
            continue

        severity = _optional_risk_label(report.risk_score_snapshot)
        rows.append(
            {
                "id": report.id,
                "reporter": report.user.get_full_name().strip() or report.user.username,
                "username": report.user.username,
                "role_display": report.user.get_role_display(),
                "incident_type": report.get_incident_type_display(),
                "description": report.description,
                "location_label": report.location_label or _coordinate_label(report.latitude, report.longitude),
                "risk_snapshot": report.risk_score_snapshot,
                "severity": severity,
                "severity_slug": _admin_status_slug(severity),
                "status": status,
                "status_slug": _admin_status_slug(status),
                "timestamp": _timestamp_label(report.created_at),
                "timestamp_relative": _time_since_label(report.created_at),
            }
        )

    return {"summary": counts, "status_filter": status_filter or "", "incidents": rows[:50]}


def _build_admin_safe_zones_payload():
    recent_incidents = IncidentReport.objects.filter(created_at__gte=timezone.now() - timedelta(days=14))
    risk_zones = RiskZone.objects.all()
    safe_havens = []
    for haven in SafeHaven.objects.order_by("name"):
        nearby_incident_count = len(
            _nearby_records(
                recent_incidents,
                haven.latitude,
                haven.longitude,
                radius_km=5,
            )
        )
        nearby_high_risk_count = sum(
            1
            for zone, _distance in _nearby_records(
                risk_zones,
                haven.latitude,
                haven.longitude,
                radius_km=5,
            )
            if zone.risk_score >= 75
        )
        safe_havens.append(
            {
                "id": haven.id,
                "name": haven.name,
                "type": haven.get_type_display(),
                "type_slug": _admin_status_slug(haven.type),
                "address": haven.address,
                "phone": haven.phone or "--",
                "is_open_24_7": haven.is_open_24_7,
                "coordinates": _coordinate_label(haven.latitude, haven.longitude),
                "nearby_incidents": nearby_incident_count,
                "nearby_high_risk_zones": nearby_high_risk_count,
            }
        )

    return {
        "summary": {
            "total": len(safe_havens),
            "open_24_7": sum(1 for haven in safe_havens if haven["is_open_24_7"]),
            "police": SafeHaven.objects.filter(type="police").count(),
            "hospital": SafeHaven.objects.filter(type="hospital").count(),
            "business": SafeHaven.objects.filter(type="business").count(),
            "public": SafeHaven.objects.filter(type="public").count(),
        },
        "safe_havens": safe_havens,
    }


def _build_admin_analytics_payload():
    today = timezone.localdate()
    daily_activity = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        daily_activity.append(
            {
                "label": day.strftime("%b %d"),
                "incidents": IncidentReport.objects.filter(created_at__date=day).count(),
                "sos_alerts": EmergencyAlert.objects.filter(timestamp__date=day).count(),
                "checkins": CheckIn.objects.filter(timestamp__date=day).count(),
            }
        )

    risk_distribution = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for zone in RiskZone.objects.all():
        risk_distribution[_normalize_risk_label(zone.risk_score)] += 1

    hourly_counter = Counter()
    for record in CrimeRecord.objects.order_by("-time")[:100]:
        hourly_counter[record.time.hour] += 1
    for alert in EmergencyAlert.objects.order_by("-timestamp")[:100]:
        hourly_counter[alert.timestamp.hour] += 1
    hourly_patterns = [
        {"hour": f"{hour:02d}:00", "count": count}
        for hour, count in sorted(hourly_counter.items(), key=lambda item: item[1], reverse=True)[:8]
    ]

    hotspots = []
    recent_incidents = IncidentReport.objects.filter(created_at__gte=timezone.now() - timedelta(days=14))
    for zone in RiskZone.objects.order_by("-risk_score", "city")[:8]:
        nearby_incident_count = len(
            _nearby_records(
                recent_incidents,
                zone.latitude,
                zone.longitude,
                radius_km=5,
            )
        )
        zone_payload = _serialize_admin_risk_zone(zone)
        zone_payload["nearby_incidents"] = nearby_incident_count
        hotspots.append(zone_payload)

    return {
        "summary": {
            "risk_predictions": RiskPrediction.objects.count(),
            "crime_records": CrimeRecord.objects.count(),
            "incidents": IncidentReport.objects.count(),
            "sos_alerts": EmergencyAlert.objects.count(),
            "checkins": CheckIn.objects.count(),
        },
        "daily_activity": daily_activity,
        "risk_distribution": risk_distribution,
        "hourly_patterns": hourly_patterns,
        "hotspots": hotspots,
    }


def _build_admin_cultural_payload():
    category_lookup = dict(CulturalGuide.CATEGORY_CHOICES)
    guides = list(CulturalGuide.objects.order_by("language", "category", "title"))
    language_counts = Counter(guide.language for guide in guides)
    category_counts = Counter(category_lookup.get(guide.category, guide.category.title()) for guide in guides)
    dataset_context = _load_city_crime_context()
    top_dataset_cities = sorted(dataset_context.values(), key=lambda item: item["report_count"], reverse=True)[:6]

    scam_reports = [
        {
            "reporter": report.user.username,
            "location_label": report.location_label or _coordinate_label(report.latitude, report.longitude),
            "description": report.description,
            "created_at": _timestamp_label(report.created_at),
        }
        for report in IncidentReport.objects.select_related("user").filter(incident_type="scam").order_by("-created_at")[:10]
    ]

    scam_zones = [_serialize_admin_risk_zone(zone) for zone in RiskZone.objects.filter(risk_type="scam").order_by("-risk_score")[:10]]

    return {
        "summary": {
            "guides": len(guides),
            "languages": len(language_counts),
            "scam_reports": len(scam_reports),
            "scam_zones": len(scam_zones),
            "dataset_cities": len(dataset_context),
        },
        "guides": [
            {
                "title": guide.title or category_lookup.get(guide.category, guide.category.title()),
                "category": category_lookup.get(guide.category, guide.category.title()),
                "category_slug": _admin_status_slug(guide.category),
                "language": guide.language,
                "content": guide.content,
            }
            for guide in guides[:30]
        ],
        "language_breakdown": [{"language": language, "count": count} for language, count in sorted(language_counts.items())],
        "category_breakdown": [{"category": category, "count": count} for category, count in sorted(category_counts.items())],
        "scam_reports": scam_reports,
        "scam_zones": scam_zones,
        "dataset_cities": top_dataset_cities,
    }


def _build_admin_notifications_payload():
    active_users = SafePassageUser.objects.filter(is_active=True).exclude(email="")
    role_counts = {
        "all": active_users.count(),
        "tourist": active_users.filter(role="tourist").count(),
        "worker": active_users.filter(role="worker").count(),
        "employer": active_users.filter(role="employer").count(),
        "admin": active_users.filter(role="admin").count(),
    }
    feed = _build_recent_admin_alerts(limit=12)
    return {
        "summary": {
            "reachable_users": role_counts["all"],
            "tourists": role_counts["tourist"],
            "workers": role_counts["worker"],
            "recent_alerts": len(feed),
        },
        "role_counts": role_counts,
        "feed": feed,
        "delivery": _notification_delivery_status(),
    }


def _build_admin_logs_payload(limit=40):
    entries = []
    for user in SafePassageUser.objects.order_by("-created_at")[:12]:
        entries.append(
            {
                "category": "User",
                "category_slug": "user",
                "actor": user.username,
                "action": "Account registered",
                "detail": f"Role: {user.get_role_display()}",
                "status": "Success",
                "status_slug": "success",
                "_sort": user.created_at,
            }
        )

    for alert in EmergencyAlert.objects.select_related("user").order_by("-timestamp")[:12]:
        entries.append(
            {
                "category": "SOS",
                "category_slug": "sos",
                "actor": alert.user.username,
                "action": f"{alert.get_mode_display()} SOS triggered",
                "detail": _coordinate_label(alert.latitude, alert.longitude),
                "status": _admin_status_title(alert.status),
                "status_slug": _admin_status_slug(alert.status),
                "_sort": alert.timestamp,
            }
        )

    for report in IncidentReport.objects.select_related("user").order_by("-created_at")[:12]:
        entries.append(
            {
                "category": "Incident",
                "category_slug": "incident",
                "actor": report.user.username,
                "action": f"{report.get_incident_type_display()} reported",
                "detail": report.location_label or _coordinate_label(report.latitude, report.longitude),
                "status": _admin_status_title(report.status, "Reported"),
                "status_slug": _admin_status_slug(report.status),
                "_sort": report.created_at,
            }
        )

    for checkin in CheckIn.objects.select_related("user").order_by("-timestamp")[:12]:
        entries.append(
            {
                "category": "Check-In",
                "category_slug": "checkin",
                "actor": checkin.user.username,
                "action": f"Worker check-in: {_admin_status_title(checkin.status, 'OK')}",
                "detail": _coordinate_label(checkin.location_lat, checkin.location_lng),
                "status": _admin_status_title(checkin.status, "OK"),
                "status_slug": _admin_status_slug(checkin.status),
                "_sort": checkin.timestamp,
            }
        )

    entries.sort(key=lambda item: item["_sort"], reverse=True)
    trimmed_entries = entries[:limit]
    recent_threshold = timezone.now() - timedelta(hours=24)
    for entry in trimmed_entries:
        entry["timestamp"] = _timestamp_label(entry["_sort"])
        entry["timestamp_relative"] = _time_since_label(entry["_sort"])
        entry["is_recent"] = entry["_sort"] >= recent_threshold
        entry.pop("_sort", None)

    return {
        "summary": {
            "entries": len(trimmed_entries),
            "last_24_hours": sum(1 for entry in trimmed_entries if entry["is_recent"]),
            "sos_entries": sum(1 for entry in trimmed_entries if entry["category"] == "SOS"),
            "incident_entries": sum(1 for entry in trimmed_entries if entry["category"] == "Incident"),
        },
        "logs": trimmed_entries,
    }


def _build_admin_profile_payload(user):
    return {
        "identity": {
            "display_name": user.get_full_name().strip() or user.username,
            "username": user.username,
            "email": user.email,
            "phone": user.phone or "--",
            "member_since": _timestamp_label(user.date_joined or user.created_at),
            "last_login": _timestamp_label(user.last_login) if user.last_login else "No recorded login",
        },
        "summary": {
            "managed_users": SafePassageUser.objects.exclude(role="admin").count(),
            "open_incidents": IncidentReport.objects.exclude(status="resolved").count(),
            "open_sos_alerts": EmergencyAlert.objects.exclude(status__iexact="resolved").count(),
            "safe_havens": SafeHaven.objects.count(),
        },
        "permissions": [
            "Monitor live tourists and night workers",
            "Review SOS alerts and incidents",
            "Validate cultural and scam data",
            "Broadcast safety notifications",
        ],
    }


def _admin_page_context(request, page_title, page_description, page_key, refresh_seconds=30, **extra):
    return {
        "admin_page_title": page_title,
        "admin_page_description": page_description,
        "admin_page_key": page_key,
        "admin_auto_refresh": refresh_seconds,
        "generated_at": timezone.now(),
        **extra,
    }


def _sanitize_sos_status(value):
    normalized = _admin_status_slug(value)
    if normalized == "resolved":
        return "Resolved"
    if normalized == "in-progress":
        return "In Progress"
    return "Active"


def _sanitize_incident_status(value):
    normalized = _admin_status_slug(value)
    if normalized == "resolved":
        return "resolved"
    if normalized == "reviewing":
        return "reviewing"
    return "reported"


@admin_required
def admin_dashboard(request):
    payload = _build_admin_dashboard_payload()
    return render(
        request,
        "admin_control/dashboard.html",
        _admin_page_context(
            request,
            "Admin Dashboard",
            "Real-time system overview for users, SOS activity, risk zones, and safety operations.",
            "admin_dashboard",
            dashboard=payload,
        ),
    )


@admin_required
def admin_users(request):
    if request.method == "POST":
        target_user = SafePassageUser.objects.filter(id=request.POST.get("user_id")).exclude(id=request.user.id).first()
        action = request.POST.get("action")
        if not target_user:
            messages.error(request, "The selected user could not be updated.")
        elif action == "suspend":
            target_user.is_active = False
            target_user.save(update_fields=["is_active"])
            messages.success(request, f"{target_user.username} has been suspended.")
        elif action == "activate":
            target_user.is_active = True
            target_user.save(update_fields=["is_active"])
            messages.success(request, f"{target_user.username} has been reactivated.")
        return redirect("admin_users")

    role_filter = (request.GET.get("role") or "").strip()
    state_filter = (request.GET.get("state") or "").strip()
    payload = _build_admin_users_payload(role_filter or None, state_filter or None)
    return render(
        request,
        "admin_control/users.html",
        _admin_page_context(
            request,
            "User Management",
            "View live user activity, filter by role, and suspend or reactivate accounts when needed.",
            "admin_users",
            users_payload=payload,
        ),
    )


@admin_required
def admin_risk_monitor(request):
    payload = _build_admin_risk_monitor_payload()
    return render(
        request,
        "admin_control/risk_monitor.html",
        _admin_page_context(
            request,
            "Live Risk Monitoring",
            "Monitor tracked user locations, zone intensity, and recent crime signals across the system.",
            "admin_risk_monitor",
            risk_monitor=payload,
        ),
    )


@admin_required
def admin_sos_alerts(request):
    if request.method == "POST":
        alert = EmergencyAlert.objects.filter(id=request.POST.get("alert_id")).first()
        if not alert:
            messages.error(request, "The selected SOS alert was not found.")
        else:
            alert.status = _sanitize_sos_status(request.POST.get("status"))
            alert.save(update_fields=["status"])
            messages.success(request, f"SOS alert #{alert.id} marked as {alert.status}.")
        return redirect("admin_sos_alerts")

    payload = _build_admin_sos_payload((request.GET.get("status") or "").strip() or None)
    return render(
        request,
        "admin_control/sos_alerts.html",
        _admin_page_context(
            request,
            "SOS Alert Management",
            "Track live emergency activations, review their locations, and move each alert through the response workflow.",
            "admin_sos_alerts",
            sos_payload=payload,
        ),
    )


@admin_required
def admin_incidents(request):
    if request.method == "POST":
        report = IncidentReport.objects.filter(id=request.POST.get("incident_id")).first()
        if not report:
            messages.error(request, "The selected incident was not found.")
        else:
            report.status = _sanitize_incident_status(request.POST.get("status"))
            report.save(update_fields=["status"])
            messages.success(request, f"Incident #{report.id} moved to {report.get_status_display()}.")
        return redirect("admin_incidents")

    payload = _build_admin_incidents_payload((request.GET.get("status") or "").strip() or None)
    return render(
        request,
        "admin_control/incidents.html",
        _admin_page_context(
            request,
            "Incident Management",
            "Review user-submitted incident reports, validate severity, and keep the case pipeline updated.",
            "admin_incidents",
            incidents_payload=payload,
        ),
    )


@admin_required
def admin_safe_zones(request):
    payload = _build_admin_safe_zones_payload()
    return render(
        request,
        "admin_control/safe_zones.html",
        _admin_page_context(
            request,
            "Safe Zone Management",
            "Monitor verified safe havens, their operating readiness, and nearby risk pressure from incidents and hot zones.",
            "admin_safe_zones",
            safe_zones_payload=payload,
        ),
    )


@admin_required
def admin_analytics(request):
    payload = _build_admin_analytics_payload()
    return render(
        request,
        "admin_control/analytics.html",
        _admin_page_context(
            request,
            "AI Risk Analytics",
            "Review historical activity trends, hotspot concentration, and model-facing signals from system activity.",
            "admin_analytics",
            analytics_payload=payload,
        ),
    )


@admin_required
def admin_cultural_data(request):
    payload = _build_admin_cultural_payload()
    return render(
        request,
        "admin_control/cultural_data.html",
        _admin_page_context(
            request,
            "Cultural Data Monitoring",
            "Validate cultural advice, scam intelligence, and dataset-backed guidance used by the tourist safety engine.",
            "admin_cultural_data",
            cultural_payload=payload,
        ),
    )


@admin_required
def admin_notifications(request):
    if request.method == "POST":
        audience = (request.POST.get("audience") or "all").strip()
        subject = (request.POST.get("subject") or "").strip()
        message_body = (request.POST.get("message") or "").strip()
        if not subject or not message_body:
            messages.error(request, "Subject and message are required before sending a broadcast.")
            return redirect("admin_notifications")

        recipients = SafePassageUser.objects.filter(is_active=True).exclude(email="")
        if audience in {"tourist", "worker", "employer", "admin"}:
            recipients = recipients.filter(role=audience)

        recipient_list = list(recipients.values_list("email", flat=True).distinct())
        if not recipient_list:
            messages.warning(request, "No active recipients with email addresses were available for this broadcast.")
            return redirect("admin_notifications")

        delivery_status = _notification_delivery_status()
        if not delivery_status["configured"]:
            messages.error(
                request,
                "SMTP notification delivery is not configured correctly yet. "
                + " ".join(delivery_status["issues"]),
            )
            return redirect("admin_notifications")

        sender = delivery_status["sender"]
        try:
            delivered, failed_recipients = _send_broadcast_notifications(
                subject,
                message_body,
                sender,
                recipient_list,
            )
        except (SMTPException, OSError, socket.error) as exc:
            messages.error(
                request,
                f"SMTP notification delivery failed before the broadcast could be completed: {exc}",
            )
            return redirect("admin_notifications")

        if delivered and failed_recipients:
            messages.warning(
                request,
                f"Broadcast partially delivered: {delivered} of {len(recipient_list)} users received the email. "
                f"{len(failed_recipients)} recipient(s) failed.",
            )
        elif delivered:
            messages.success(
                request,
                f"Broadcast delivered to {delivered} user(s) through {delivery_status['backend_label']}.",
            )
        else:
            messages.error(
                request,
                "SMTP accepted the request, but no notification emails were delivered.",
            )
        return redirect("admin_notifications")

    payload = _build_admin_notifications_payload()
    return render(
        request,
        "admin_control/notifications.html",
        _admin_page_context(
            request,
            "Notification System",
            "Broadcast warnings to active users and review the latest alert feed before sending system-wide notifications.",
            "admin_notifications",
            notifications_payload=payload,
        ),
    )


@admin_required
def admin_system_logs(request):
    payload = _build_admin_logs_payload()
    return render(
        request,
        "admin_control/system_logs.html",
        _admin_page_context(
            request,
            "System Logs",
            "Track user actions, SOS activity, incidents, and worker monitoring events in one operational timeline.",
            "admin_system_logs",
            logs_payload=payload,
        ),
    )


@admin_required
def admin_profile(request):
    if request.method == "POST":
        first_name = (request.POST.get("first_name") or "").strip()
        last_name = (request.POST.get("last_name") or "").strip()
        phone = (request.POST.get("phone") or "").strip()

        # Strict validations
        if not re.match(r"^[a-zA-Z\s]{2,}$", first_name):
            messages.error(request, "First Name must contain only alphabets and be at least 2 characters.")
            return redirect("admin_profile")

        if last_name and not re.match(r"^[a-zA-Z\s]{2,}$", last_name):
            messages.error(request, "Last Name must contain only alphabets and be at least 2 characters.")
            return redirect("admin_profile")

        if not re.match(r"^\d{10}$", phone):
            messages.error(request, "Enter a valid 10-digit phone number.")
            return redirect("admin_profile")

        request.user.first_name = first_name
        request.user.last_name = last_name
        request.user.phone = phone
        request.user.save(update_fields=["first_name", "last_name", "phone"])
        messages.success(request, "Admin profile updated successfully.")
        return redirect("admin_profile")

    payload = _build_admin_profile_payload(request.user)
    return render(
        request,
        "admin_control/profile.html",
        _admin_page_context(
            request,
            "Admin Profile",
            "Manage the authenticated admin identity and review your operational access across the SafePassage system.",
            "admin_profile",
            profile_payload=payload,
            refresh_seconds=0,
        ),
    )


@login_required
def api_admin_dashboard_data(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    return JsonResponse({"status": "success", **_build_admin_dashboard_payload()})


@login_required
def api_admin_users(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    role_filter = (request.GET.get("role") or "").strip() or None
    state_filter = (request.GET.get("state") or "").strip() or None
    return JsonResponse({"status": "success", **_build_admin_users_payload(role_filter, state_filter)})


@login_required
def api_admin_risk_monitor(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    return JsonResponse({"status": "success", **_build_admin_risk_monitor_payload()})


@login_required
def api_admin_sos_alerts(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    status_filter = (request.GET.get("status") or "").strip() or None
    return JsonResponse({"status": "success", **_build_admin_sos_payload(status_filter)})


@login_required
def api_admin_incidents(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    status_filter = (request.GET.get("status") or "").strip() or None
    return JsonResponse({"status": "success", **_build_admin_incidents_payload(status_filter)})


@login_required
def api_admin_safe_zones(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    return JsonResponse({"status": "success", **_build_admin_safe_zones_payload()})


@login_required
def api_admin_analytics(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    return JsonResponse({"status": "success", **_build_admin_analytics_payload()})


@login_required
def api_admin_cultural_data(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    return JsonResponse({"status": "success", **_build_admin_cultural_payload()})


@login_required
def api_admin_notifications(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    return JsonResponse({"status": "success", **_build_admin_notifications_payload()})


@login_required
def api_admin_logs(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    return JsonResponse({"status": "success", **_build_admin_logs_payload()})


@login_required
def api_admin_profile(request):
    guard_response = _admin_api_guard(request)
    if guard_response:
        return guard_response
    return JsonResponse({"status": "success", **_build_admin_profile_payload(request.user)})

@login_required
def sos_alert(request):
    user = request.user
    lat = request.GET.get('lat', 'Unknown')
    lng = request.GET.get('lng', 'Unknown')
    mode = request.GET.get('mode', 'silent')
    
    # Use the central dispatch helper for consistency
    try:
        lat = float(lat)
        lng = float(lng)
    except (ValueError, TypeError):
        lat, lng = _resolve_user_coordinates(user)
    
    if lat is None or lng is None:
        return JsonResponse({"status": "error", "message": "Location required"}, status=400)

    try:
        response_payload = _dispatch_emergency_alert(user, lat, lng, mode, request=request)
        return JsonResponse(response_payload)
    except Exception as e:
        return JsonResponse({"status": "Error sending alert", "error": str(e)}, status=500)

def api_sos_acknowledge(request, token):
    alert = get_object_or_404(EmergencyAlert, token=token)
    if not alert.acknowledged_at:
        alert.acknowledged_at = timezone.now()
        alert.status = "Acknowledged"
        alert.save()
    
    return render(request, "sos_confirm.html", {"alert": alert})
