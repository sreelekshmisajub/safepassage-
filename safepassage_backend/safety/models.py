from django.db import models
from django.contrib.auth.models import AbstractUser

class SafePassageUser(AbstractUser):
    ROLE_CHOICES = (
        ('tourist', 'Tourist'),
        ('worker', 'Night Worker'),
        ('employer', 'Employer'),
        ('admin', 'Admin'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='tourist')
    phone = models.CharField(max_length=15, blank=True, null=True)
    profile_image = models.ImageField(upload_to='profile_images/', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RiskPrediction(models.Model):
    location = models.CharField(max_length=200)
    year = models.IntegerField()
    crime_value = models.FloatField()
    predicted_risk = models.CharField(max_length=50)
    risk_score = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.location} - {self.predicted_risk}"

class UserLocation(models.Model):
    user = models.ForeignKey(SafePassageUser, on_delete=models.CASCADE)
    latitude = models.FloatField()
    longitude = models.FloatField()
    timestamp = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.latitude}, {self.longitude}"

class RiskZone(models.Model):
    RISK_TYPES = (
        ('crime', 'Crime'),
        ('scam', 'Scam'),
        ('weather', 'Weather'),
        ('crowd', 'Crowd'),
    )

    latitude = models.FloatField()
    longitude = models.FloatField()
    risk_type = models.CharField(max_length=20, choices=RISK_TYPES)
    risk_score = models.IntegerField()
    description = models.TextField()
    city = models.CharField(max_length=100, default="New Delhi")

    def __str__(self):
        return f"{self.risk_type} ({self.risk_score}) at {self.latitude}, {self.longitude}"

class EmergencyAlert(models.Model):
    MODE_CHOICES = (
        ('loud', 'Loud'),
        ('silent', 'Silent'),
    )

    user = models.ForeignKey(SafePassageUser, on_delete=models.CASCADE)
    latitude = models.FloatField()
    longitude = models.FloatField()
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='silent')
    timestamp = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default="Active")

    def __str__(self):
        return f"SOS by {self.user.email} ({self.mode}) at {self.timestamp}"


class IncidentReport(models.Model):
    INCIDENT_CHOICES = (
        ('theft', 'Theft'),
        ('scam', 'Scam'),
        ('assault', 'Assault'),
        ('harassment', 'Harassment'),
        ('medical', 'Medical'),
        ('other', 'Other'),
    )
    STATUS_CHOICES = (
        ('reported', 'Reported'),
        ('reviewing', 'Reviewing'),
        ('resolved', 'Resolved'),
    )

    user = models.ForeignKey(SafePassageUser, on_delete=models.CASCADE, related_name='incident_reports')
    incident_type = models.CharField(max_length=20, choices=INCIDENT_CHOICES, default='other')
    description = models.TextField()
    location_label = models.CharField(max_length=200, blank=True, default='')
    latitude = models.FloatField()
    longitude = models.FloatField()
    image_name = models.CharField(max_length=255, blank=True, default='')
    risk_score_snapshot = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='reported')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_incident_type_display()} reported by {self.user.username}"


class CulturalGuide(models.Model):
    CATEGORY_CHOICES = [
        ('do', 'Do'),
        ('dont', 'Dont'),
        ('scam', 'Scam'),
        ('phrase', 'Emergency Phrase'),
    ]

    language = models.CharField(max_length=10, default='en')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='do')
    title = models.CharField(max_length=200, default='')
    content = models.TextField(default='')

    def __str__(self):
        return f"{self.language} - {self.title}"


class EmergencyContact(models.Model):
    RELATIONSHIP_CHOICES = [
        ('parent', 'Parent'),
        ('spouse', 'Spouse'),
        ('sibling', 'Sibling'),
        ('friend', 'Friend'),
        ('relative', 'Relative'),
        ('colleague', 'Colleague'),
        ('other', 'Other'),
    ]
    
    user = models.ForeignKey(SafePassageUser, on_delete=models.CASCADE, related_name='emergency_contacts')
    name = models.CharField(max_length=100)
    relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True, null=True)
    whatsapp_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.get_relationship_display()}) - {self.user.username}"


class JourneyDetail(models.Model):
    user = models.OneToOneField(SafePassageUser, on_delete=models.CASCADE, related_name='journey')
    arrival_date = models.DateField()
    departure_date = models.DateField()
    current_location = models.CharField(max_length=500)
    hotel_address = models.TextField(blank=True, null=True)
    flight_number = models.CharField(max_length=20, blank=True, null=True)
    travel_insurance = models.BooleanField(default=False)
    insurance_provider = models.CharField(max_length=100, blank=True, null=True)
    insurance_policy_number = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Journey for {self.user.username} ({self.arrival_date} to {self.departure_date})"


class TouristProfile(models.Model):
    user = models.OneToOneField(SafePassageUser, on_delete=models.CASCADE, related_name='tourist_profile')
    full_name = models.CharField(max_length=200)
    nationality = models.CharField(max_length=100)
    passport_number = models.CharField(max_length=50, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    blood_group = models.CharField(max_length=10, blank=True, null=True)
    allergies = models.TextField(blank=True, null=True)
    medications = models.TextField(blank=True, null=True)
    emergency_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile for {self.user.username}"


class WorkerProfile(models.Model):
    user = models.OneToOneField(SafePassageUser, on_delete=models.CASCADE, related_name='worker_profile')
    employee_id = models.CharField(max_length=50)
    company_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    designation = models.CharField(max_length=100, blank=True, default='')
    department = models.CharField(max_length=100, blank=True, default='')
    work_location = models.CharField(max_length=200, blank=True, default='')
    home_address = models.TextField(blank=True, default='')
    emergency_contact_name = models.CharField(max_length=100, blank=True, default='')
    emergency_contact_phone = models.CharField(max_length=20, blank=True, default='')
    blood_group = models.CharField(max_length=10, blank=True, default='')
    usual_shift_start = models.TimeField(null=True, blank=True)
    usual_shift_end = models.TimeField(null=True, blank=True)
    leave_dates = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.user.username


class CrimeRecord(models.Model):
    area_name = models.CharField(max_length=100)
    latitude = models.FloatField()
    longitude = models.FloatField()
    crime_type = models.CharField(max_length=100)
    time = models.DateTimeField()

    def __str__(self):
        return f"{self.crime_type} at {self.area_name}"


class Shift(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('pending', 'Pending'),
    )
    user = models.ForeignKey(SafePassageUser, on_delete=models.CASCADE, related_name='shifts')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    company_name = models.CharField(max_length=200, blank=True, null=True)
    
    def __str__(self):
        return f"{self.user.username} Shift - {self.status}"


class SafeHaven(models.Model):
    HAVEN_TYPES = (
        ('police', 'Police Station'),
        ('hospital', 'Hospital'),
        ('business', 'Verified Safe Business'),
        ('public', '24/7 Public Space'),
    )
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=20, choices=HAVEN_TYPES)
    latitude = models.FloatField()
    longitude = models.FloatField()
    address = models.TextField()
    phone = models.CharField(max_length=20, blank=True, null=True)
    is_open_24_7 = models.BooleanField(default=True)
    
    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"


class CheckIn(models.Model):
    STATUS_CHOICES = (
        ('ok', "I'm Safe"),
        ('missed', 'Missed'),
        ('assistance', 'Need Assistance'),
        ('checkout', 'Checked Out'),
    )
    user = models.ForeignKey(SafePassageUser, on_delete=models.CASCADE)
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name='checkins')
    timestamp = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ok')
    location_lat = models.FloatField(null=True, blank=True)
    location_lng = models.FloatField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.user.username} Check-in at {self.timestamp}"
