from django.contrib import admin

from .models import SafePassageUser, CulturalGuide, RiskZone, EmergencyAlert, IncidentReport

admin.site.register(SafePassageUser)
admin.site.register(CulturalGuide)
admin.site.register(RiskZone)
admin.site.register(EmergencyAlert)
admin.site.register(IncidentReport)
