import json
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import CheckIn, CulturalGuide, EmergencyAlert, EmergencyContact, IncidentReport, RiskZone, SafeHaven, SafePassageUser, Shift, UserLocation, WorkerProfile


class LandingPageTests(TestCase):
    def setUp(self):
        self.tourist = SafePassageUser.objects.create_user(
            username="landing-tourist@example.com",
            email="landing-tourist@example.com",
            password="StrongPass123!",
            role="tourist",
            first_name="Mira",
        )
        self.worker = SafePassageUser.objects.create_user(
            username="landing-worker@example.com",
            email="landing-worker@example.com",
            password="StrongPass123!",
            role="worker",
            first_name="Ravi",
        )
        UserLocation.objects.create(user=self.tourist, latitude=9.9312, longitude=76.2673)
        RiskZone.objects.create(
            latitude=9.9312,
            longitude=76.2673,
            risk_type="crime",
            risk_score=81,
            description="Late-night theft activity reported.",
            city="Kochi Central",
        )
        SafeHaven.objects.create(
            name="Town Hall Police Station",
            type="police",
            latitude=9.9325,
            longitude=76.2681,
            address="Town Hall Road, Kochi",
            is_open_24_7=True,
        )
        CulturalGuide.objects.create(
            language="en",
            category="do",
            title="Temple entry",
            content="Respect local dress guidance before entering temple compounds.",
        )
        IncidentReport.objects.create(
            user=self.tourist,
            incident_type="scam",
            description="Fake guide activity reported near the ferry point.",
            location_label="Ferry Point",
            latitude=9.9318,
            longitude=76.2676,
            risk_score_snapshot=72,
        )
        EmergencyAlert.objects.create(
            user=self.worker,
            latitude=9.9341,
            longitude=76.2702,
            mode="silent",
            status="Active",
        )
        Shift.objects.create(
            user=self.worker,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=8),
            actual_start=timezone.now(),
            status="active",
        )

    def test_landing_page_renders_live_summary_for_guest(self):
        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stay Safe Anywhere")
        self.assertContains(response, "AI-Powered Protection")
        self.assertEqual(response.context["landing_stats"]["protected_users"], 2)
        self.assertEqual(response.context["landing_stats"]["monitored_risk_zones"], 1)
        self.assertEqual(response.context["landing_routes"]["login"], "/login/")

    def test_landing_page_uses_role_aware_routes_for_tourist(self):
        self.client.force_login(self.tourist)
        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["landing_routes"]["safe_route"], "/map/?tab=routes")
        self.assertEqual(response.context["landing_routes"]["sos"], "/sos/")
        self.assertTrue(response.context["landing_config"]["can_trigger_emergency"])


class TouristApiTests(TestCase):
    def setUp(self):
        self.user = SafePassageUser.objects.create_user(
            username="tourist@example.com",
            email="tourist@example.com",
            password="StrongPass123!",
            role="tourist",
            first_name="Ava",
        )
        self.client.force_login(self.user)

        self.risk_zone = RiskZone.objects.create(
            latitude=9.9312,
            longitude=76.2673,
            risk_type="scam",
            risk_score=82,
            description="Scam hotspot near transport interchange.",
            city="Kochi Central",
        )
        self.safe_haven = SafeHaven.objects.create(
            name="Marine Drive Police Aid Post",
            type="police",
            latitude=9.9320,
            longitude=76.2679,
            address="Marine Drive, Kochi",
            phone="+91-0000000000",
            is_open_24_7=True,
        )
        CulturalGuide.objects.create(
            language="en",
            category="do",
            title="Temple etiquette",
            content="Carry a scarf and remove shoes before entering temple areas.",
        )
        CulturalGuide.objects.create(
            language="en",
            category="dont",
            title="Public transport conduct",
            content="Avoid loud arguments in crowded public transport areas.",
        )
        CulturalGuide.objects.create(
            language="en",
            category="scam",
            title="Taxi overcharge alert",
            content="Confirm taxi fare before boarding when transport scams are reported nearby.",
        )
        IncidentReport.objects.create(
            user=self.user,
            incident_type="scam",
            description="Taxi overcharging reported near the interchange.",
            location_label="Kochi Central Bus Hub",
            latitude=9.9316,
            longitude=76.2681,
            risk_score_snapshot=78,
        )
        EmergencyContact.objects.create(
            user=self.user,
            name="Maya",
            relationship="friend",
            phone="+91-9999999999",
            is_primary=True,
        )

    def test_predict_risk_endpoint_returns_expected_shape(self):
        response = self.client.get(reverse("api_predict_risk"), {"lat": 9.9312, "lng": 76.2673})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertIn(payload["risk_label"], {"LOW", "MEDIUM", "HIGH"})
        self.assertIn("breakdown", payload)
        self.assertIn("weather", payload)
        self.assertIn("nearby_resources", payload)
        self.assertEqual(payload["location"], "Kochi Central")

    def test_incidents_endpoint_returns_zone_backed_alerts(self):
        response = self.client.get(reverse("api_incidents"), {"lat": 9.9312, "lng": 76.2673})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertGreaterEqual(payload["count"], 1)
        self.assertIn("risk-zone", [item["source"] for item in payload["alerts"]])

    def test_report_incident_creates_database_record(self):
        response = self.client.post(
            reverse("api_report_incident"),
            {
                "lat": 9.9312,
                "lng": 76.2673,
                "incident_type": "theft",
                "description": "Phone snatching reported near ferry terminal.",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertTrue(IncidentReport.objects.filter(user=self.user, incident_type="theft").exists())

    def test_translate_endpoint_uses_emergency_phrasebook(self):
        response = self.client.post(
            reverse("api_translate"),
            data=json.dumps(
                {
                    "text": "Help me, I am in danger",
                    "target_language": "hi",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["translation_mode"], "phrasebook")
        self.assertIn("hi", payload["translations"])

    def test_translate_endpoint_matches_common_emergency_variants(self):
        response = self.client.post(
            reverse("api_translate"),
            data=json.dumps(
                {
                    "text": "Please help me right now",
                    "target_language": "hi",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["translation_mode"], "intent-match")
        self.assertTrue(payload["translated_text"])
        self.assertIn("Matched your message", payload["note"])

    def test_translate_endpoint_reports_unavailable_for_unsupported_phrase(self):
        response = self.client.post(
            reverse("api_translate"),
            data=json.dumps(
                {
                    "text": "Where can I buy a souvenir?",
                    "target_language": "hi",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["translation_mode"], "unavailable")
        self.assertEqual(payload["translated_text"], "")
        self.assertIn("Live translation is unavailable", payload["note"])

    def test_emergency_endpoint_creates_alert(self):
        response = self.client.post(
            reverse("api_emergency"),
            data=json.dumps(
                {
                    "latitude": 9.9312,
                    "longitude": 76.2673,
                    "mode": "silent",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertTrue(EmergencyAlert.objects.filter(user=self.user, mode="silent").exists())
        self.assertIn("delivery_channels", payload)
        self.assertEqual(payload["delivery_channels"]["sms_contacts"], 1)
        self.assertEqual(payload["delivery_channels"]["whatsapp_contacts"], 1)

    def test_tourist_dashboard_renders(self):
        response = self.client.get(reverse("tourist_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "touristDashboardRoot")
        self.assertContains(response, "Detecting live location")

    def test_tourist_dashboard_hub_renders(self):
        response = self.client.get(reverse("tourist_dashboard_hub"), {"mode": "tourist"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Map & Routes")
        self.assertContains(response, "Live Safety Map")

    def test_safe_route_endpoint_returns_route_payload(self):
        response = self.client.get(
            reverse("api_safe_route"),
            {
                "source_lat": 9.9312,
                "source_lng": 76.2673,
                "dest_lat": 9.9422,
                "dest_lng": 76.2851,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertIn("route_summary", payload)
        self.assertGreaterEqual(len(payload["route"]), 2)
        self.assertEqual(payload["default_route_tier"], "low")
        self.assertEqual([item["id"] for item in payload["route_options"]], ["low", "medium", "high"])
        self.assertIn("corridor_hotspot_definition", payload)

    def test_safe_route_endpoint_accepts_destination_place(self):
        response = self.client.get(
            reverse("api_safe_route"),
            {
                "source_lat": 9.9312,
                "source_lng": 76.2673,
                "destination_place": f"safe-haven-{self.safe_haven.id}",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["destination"]["name"], self.safe_haven.name)

    def test_place_search_returns_india_results(self):
        response = self.client.get(reverse("api_place_search"), {"q": "Kochi"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertGreaterEqual(len(payload["results"]), 1)
        self.assertTrue(any("kochi" in item["name"].lower() for item in payload["results"]))

    def test_embassy_info_requires_known_nationality_for_specific_contact(self):
        response = self.client.get(reverse("api_embassy_info"), {"lat": 9.9312, "lng": 76.2673})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["embassy_name"], "Embassy details need your nationality")
        self.assertEqual(payload["phone"], "")

    def test_safe_route_page_renders(self):
        response = self.client.get(reverse("tourist_safe_route"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/map/?tab=routes")

    def test_cultural_safety_page_renders(self):
        response = self.client.get(reverse("tourist_cultural_safety"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cultural Safety Guide")
        self.assertContains(response, "/cultural-data/")
        self.assertContains(response, "Scam Awareness")
        self.assertContains(response, "Search a place in India")

    def test_cultural_data_endpoint_returns_aggregated_live_payload(self):
        response = self.client.get(reverse("cultural_data"), {"lat": 9.9312, "lng": 76.2673})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["location"], "Kochi Central")
        self.assertIn("risk_score", payload)
        self.assertTrue(payload["dos"])
        self.assertTrue(payload["donts"])
        self.assertTrue(payload["quick_help"])
        self.assertTrue(payload["real_time_alerts"])
        self.assertTrue(payload["restricted_zones"])
        self.assertIn("embassy", payload["emergency"])
        self.assertIn("official_lines", payload["emergency"])
        self.assertIn("location_insights", payload)
        self.assertIn("cultural_risk_score_meta", payload)

    def test_scam_alerts_page_renders_live_location_search(self):
        response = self.client.get(reverse("tourist_scam_alerts"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Live Scam Alerts")
        self.assertContains(response, "Search a place in India")
        self.assertContains(response, "/api/place-search/")
        self.assertContains(response, "Scam Watch Feed")

    def test_sos_page_renders_live_dispatch_controls(self):
        response = self.client.get(reverse("tourist_sos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SOS and Panic Center")
        self.assertContains(response, "SafePassage Help")


class WorkerModuleTests(TestCase):
    def setUp(self):
        self.user = SafePassageUser.objects.create_user(
            username="worker@example.com",
            email="worker@example.com",
            password="StrongPass123!",
            role="worker",
            first_name="Noah",
        )
        self.client.force_login(self.user)
        self.risk_zone = RiskZone.objects.create(
            latitude=9.9674,
            longitude=76.2454,
            risk_type="crime",
            risk_score=68,
            description="Recent night-time crime concentration near the junction.",
            city="Ernakulam Junction",
        )
        self.safe_haven = SafeHaven.objects.create(
            name="24/7 Worker Support Hub",
            type="business",
            latitude=9.9680,
            longitude=76.2460,
            address="MG Road, Kochi",
            phone="+91-8888888888",
            is_open_24_7=True,
        )
        UserLocation.objects.create(user=self.user, latitude=9.9674, longitude=76.2454)
        WorkerProfile.objects.create(
            user=self.user,
            employee_id="NW-1001",
            company_name="SafePassage Night Ops",
            phone="9876543210",
        )
        IncidentReport.objects.create(
            user=self.user,
            incident_type="harassment",
            description="Late-night harassment reported near the station road.",
            location_label="Ernakulam Junction",
            latitude=9.9679,
            longitude=76.2458,
            risk_score_snapshot=72,
        )

    def test_worker_dashboard_page_renders_live_integration_hooks(self):
        response = self.client.get(reverse("worker_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Night Worker Dashboard")
        self.assertContains(response, "/api/worker/dashboard-data/")

    def test_all_worker_pages_render_successfully(self):
        for page_name in (
            "worker_dashboard",
            "worker_shift_management",
            "worker_safe_route",
            "worker_safe_havens",
            "worker_checkin",
            "worker_map",
            "worker_sos",
            "worker_alerts",
            "worker_profile",
        ):
            with self.subTest(page_name=page_name):
                response = self.client.get(reverse(page_name))
                self.assertEqual(response.status_code, 200)

    def test_worker_map_page_renders_manual_search_controls(self):
        response = self.client.get(reverse("worker_map"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search a place in India")
        self.assertContains(response, "Build Safe Route")
        self.assertNotContains(response, "Use My Tracked Location")
        self.assertContains(response, "/api/worker/place-search/")

    def test_worker_sos_page_renders_phone_target_form(self):
        response = self.client.get(reverse("worker_sos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SOS Phone Target")
        self.assertContains(response, "Save SOS Target")
        self.assertContains(response, "/api/worker/sos-target/")

    def test_worker_sos_target_endpoint_updates_worker_profile_phone(self):
        response = self.client.post(
            reverse("api_worker_sos_target"),
            data=json.dumps({"name": "Night Supervisor", "phone": "9123456789"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["contact"]["phone"], "9123456789")
        self.assertEqual(payload["contact"]["name"], "Night Supervisor")

        worker_profile = WorkerProfile.objects.get(user=self.user)
        self.assertEqual(worker_profile.emergency_contact_name, "Night Supervisor")
        self.assertEqual(worker_profile.emergency_contact_phone, "9123456789")

    def test_worker_dashboard_page_exposes_saved_location_fallback_script(self):
        response = self.client.get(reverse("worker_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "window.dashboardFallbackLocation")
        self.assertContains(response, "9.967400")

    def test_worker_dashboard_data_returns_live_payload(self):
        response = self.client.get(reverse("api_worker_dashboard_data"), {"lat": 9.9674, "lng": 76.2454})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["location"], "Ernakulam Junction")
        self.assertIn("risk_score", payload)
        self.assertIn("nearby_safe_havens", payload)
        self.assertIn("alerts", payload)

    def test_worker_dashboard_data_uses_saved_location_without_query_coordinates(self):
        response = self.client.get(reverse("api_worker_dashboard_data"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["coordinates"]["latitude"], 9.9674)
        self.assertEqual(payload["coordinates"]["longitude"], 76.2454)

    def test_worker_shift_start_and_checkin_flow(self):
        start_response = self.client.post(
            reverse("start_shift"),
            data=json.dumps({"lat": 9.9674, "lng": 76.2454}),
            content_type="application/json",
        )
        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(start_response.json()["initial_checkin"]["location_label"], "Ernakulam Junction")
        self.assertTrue(Shift.objects.filter(user=self.user, status="active").exists())
        self.assertTrue(CheckIn.objects.filter(user=self.user, status="ok").exists())

        checkin_response = self.client.post(
            reverse("submit_checkin"),
            data=json.dumps({"status": "ok", "lat": 9.9674, "lng": 76.2454}),
            content_type="application/json",
        )
        self.assertEqual(checkin_response.status_code, 200)
        self.assertTrue(CheckIn.objects.filter(user=self.user, status="ok").exists())

    def test_worker_shift_preferences_endpoint_saves_timings_and_leave_dates(self):
        today = timezone.localdate().isoformat()
        response = self.client.post(
            reverse("api_worker_shift_preferences"),
            data=json.dumps(
                {
                    "usual_shift_start": "20:00",
                    "usual_shift_end": "05:00",
                    "leave_dates": [today],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.user.worker_profile.refresh_from_db()
        self.assertEqual(self.user.worker_profile.usual_shift_start.strftime("%H:%M"), "20:00")
        self.assertEqual(self.user.worker_profile.usual_shift_end.strftime("%H:%M"), "05:00")
        self.assertEqual(self.user.worker_profile.leave_dates, [today])
        self.assertTrue(payload["schedule_preferences"]["leave_today"])

    def test_worker_shift_escalation_auto_dispatches_after_two_minute_missed_checkin(self):
        local_now = timezone.localtime()
        worker_profile = self.user.worker_profile
        worker_profile.usual_shift_start = (local_now - timedelta(minutes=3)).time().replace(second=0, microsecond=0)
        worker_profile.usual_shift_end = (local_now + timedelta(hours=7, minutes=57)).time().replace(second=0, microsecond=0)
        worker_profile.leave_dates = []
        worker_profile.emergency_contact_name = "Night Supervisor"
        worker_profile.emergency_contact_phone = "9123456789"
        worker_profile.save(update_fields=["usual_shift_start", "usual_shift_end", "leave_dates", "emergency_contact_name", "emergency_contact_phone"])

        response = self.client.get(reverse("api_worker_shift_escalation"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["escalation_level"], "sos")
        self.assertEqual(payload["type"], "start")
        self.assertEqual(payload["auto_sos_after_minutes"], 2)
        self.assertEqual(payload["delivery_channels"]["sms_contacts"], 1)
        self.assertEqual(payload["delivery_channels"]["whatsapp_contacts"], 1)
        self.assertTrue(EmergencyAlert.objects.filter(user=self.user, mode="silent").exists())

    def test_worker_shift_escalation_auto_dispatches_after_two_minute_missed_checkout(self):
        local_now = timezone.localtime()
        worker_profile = self.user.worker_profile
        worker_profile.usual_shift_start = (local_now - timedelta(hours=8)).time().replace(second=0, microsecond=0)
        worker_profile.usual_shift_end = (local_now - timedelta(minutes=3)).time().replace(second=0, microsecond=0)
        worker_profile.leave_dates = []
        worker_profile.emergency_contact_name = "Shift Manager"
        worker_profile.emergency_contact_phone = "9234567890"
        worker_profile.save(update_fields=["usual_shift_start", "usual_shift_end", "leave_dates", "emergency_contact_name", "emergency_contact_phone"])

        Shift.objects.create(
            user=self.user,
            start_time=timezone.now() - timedelta(hours=8),
            end_time=timezone.now() - timedelta(minutes=3),
            actual_start=timezone.now() - timedelta(hours=8),
            status="active",
            company_name=worker_profile.company_name,
        )

        response = self.client.get(reverse("api_worker_shift_escalation"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["escalation_level"], "sos")
        self.assertEqual(payload["type"], "end")
        self.assertEqual(payload["auto_sos_after_minutes"], 2)
        self.assertTrue(payload["auto_sos_dispatched"])
        self.assertTrue(EmergencyAlert.objects.filter(user=self.user, mode="silent").exists())

    def test_worker_shift_escalation_skips_auto_sos_on_leave_day(self):
        today = timezone.localdate().isoformat()
        local_now = timezone.localtime()
        worker_profile = self.user.worker_profile
        worker_profile.usual_shift_start = (local_now - timedelta(minutes=10)).time().replace(second=0, microsecond=0)
        worker_profile.usual_shift_end = (local_now + timedelta(hours=7, minutes=50)).time().replace(second=0, microsecond=0)
        worker_profile.leave_dates = [today]
        worker_profile.save(update_fields=["usual_shift_start", "usual_shift_end", "leave_dates"])

        response = self.client.get(reverse("api_worker_shift_escalation"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["escalation_level"], "leave")
        self.assertFalse(EmergencyAlert.objects.filter(user=self.user, mode="silent").exists())

    def test_worker_checkout_checkin_marks_shift_completed(self):
        self.client.post(
            reverse("start_shift"),
            data=json.dumps({"lat": 9.9674, "lng": 76.2454}),
            content_type="application/json",
        )

        response = self.client.post(
            reverse("submit_checkin"),
            data=json.dumps({"status": "checkout", "lat": 9.9674, "lng": 76.2454}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(CheckIn.objects.filter(user=self.user, status="checkout").exists())
        self.assertFalse(Shift.objects.filter(user=self.user, status="active").exists())

    def test_worker_safe_route_endpoint_returns_live_route_payload(self):
        response = self.client.get(
            reverse("api_worker_safe_route"),
            {
                "source_lat": 9.9674,
                "source_lng": 76.2454,
                "destination_place": f"safe-haven-{self.safe_haven.id}",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertIn("route_summary", payload)
        self.assertEqual(payload["destination"]["name"], self.safe_haven.name)
        self.assertEqual(payload["source"]["name"], "Ernakulam Junction")
        self.assertEqual(payload["default_route_tier"], "low")
        self.assertEqual([option["id"] for option in payload["route_options"]], ["low", "medium", "high"])

    def test_worker_safe_route_accepts_typed_destination_query(self):
        response = self.client.get(
            reverse("api_worker_safe_route"),
            {
                "source_lat": 9.9674,
                "source_lng": 76.2454,
                "destination_name": "Worker Support Hub",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["destination"]["name"], self.safe_haven.name)
        self.assertEqual(len(payload["route_options"]), 3)

    def test_worker_alerts_page_and_endpoint_render(self):
        page_response = self.client.get(reverse("worker_alerts"))
        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, "Worker Alerts")

        api_response = self.client.get(reverse("api_worker_alerts"), {"lat": 9.9674, "lng": 76.2454})
        self.assertEqual(api_response.status_code, 200)
        payload = api_response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["location"], "Ernakulam Junction")
        self.assertGreaterEqual(payload["count"], 1)

    def test_worker_profile_page_renders_editable_profile_form(self):
        response = self.client.get(reverse("worker_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Profile picture")
        self.assertContains(response, "Emergency contact name")
        self.assertContains(response, "Usual shift start")

    def test_worker_profile_post_updates_extended_fields(self):
        response = self.client.post(
            reverse("worker_profile"),
            {
                "first_name": "Ravi",
                "last_name": "Kumar",
                "phone": "9876543210",
                "employee_id": "NW-2207",
                "company_name": "SafeShift Logistics",
                "designation": "Field Supervisor",
                "department": "Operations",
                "work_location": "Ernakulam Junction Hub",
                "home_address": "12 MG Road, Kochi",
                "emergency_contact_name": "Anita Kumar",
                "emergency_contact_phone": "9123456789",
                "blood_group": "O+",
                "usual_shift_start": "20:00",
                "usual_shift_end": "05:00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Ravi")
        self.assertEqual(self.user.last_name, "Kumar")
        self.assertEqual(self.user.phone, "9876543210")

        worker_profile = self.user.worker_profile
        self.assertEqual(worker_profile.employee_id, "NW-2207")
        self.assertEqual(worker_profile.company_name, "SafeShift Logistics")
        self.assertEqual(worker_profile.designation, "Field Supervisor")
        self.assertEqual(worker_profile.department, "Operations")
        self.assertEqual(worker_profile.work_location, "Ernakulam Junction Hub")
        self.assertEqual(worker_profile.home_address, "12 MG Road, Kochi")
        self.assertEqual(worker_profile.emergency_contact_name, "Anita Kumar")
        self.assertEqual(worker_profile.emergency_contact_phone, "9123456789")
        self.assertEqual(worker_profile.blood_group, "O+")
        self.assertContains(response, "Worker profile updated successfully.")

    def test_worker_profile_requires_all_core_fields(self):
        response = self.client.post(
            reverse("worker_profile"),
            {
                "first_name": "Ravi",
                "last_name": "",
                "phone": "9876543210",
                "employee_id": "NW-2207",
                "company_name": "SafeShift Logistics",
                "designation": "Field Supervisor",
                "department": "Operations",
                "work_location": "Ernakulam Junction Hub",
                "home_address": "12 MG Road, Kochi",
                "emergency_contact_name": "Anita Kumar",
                "emergency_contact_phone": "9123456789",
                "blood_group": "O+",
                "usual_shift_start": "20:00",
                "usual_shift_end": "05:00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete all required worker profile fields: Last name.")
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.first_name, "Ravi")
        self.assertEqual(self.user.worker_profile.employee_id, "NW-1001")

    def test_worker_emergency_api_uses_saved_location_when_gps_is_missing(self):
        response = self.client.post(
            reverse("api_emergency"),
            data=json.dumps({"mode": "loud"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["location_source"], "saved")
        self.assertEqual(payload["coordinates"]["latitude"], 9.9674)
        self.assertEqual(payload["coordinates"]["longitude"], 76.2454)


class AdminModuleTests(TestCase):
    def setUp(self):
        self.admin_user = SafePassageUser.objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password="StrongPass123!",
            role="admin",
            is_staff=True,
            is_superuser=True,
            first_name="Admin",
        )
        self.tourist_user = SafePassageUser.objects.create_user(
            username="tourist-admin-view@example.com",
            email="tourist-admin-view@example.com",
            password="StrongPass123!",
            role="tourist",
            first_name="Lia",
        )
        self.worker_user = SafePassageUser.objects.create_user(
            username="worker-admin-view@example.com",
            email="worker-admin-view@example.com",
            password="StrongPass123!",
            role="worker",
            first_name="Omar",
        )
        UserLocation.objects.create(user=self.worker_user, latitude=9.9674, longitude=76.2454)
        self.alert = EmergencyAlert.objects.create(
            user=self.worker_user,
            latitude=9.9674,
            longitude=76.2454,
            mode="silent",
            status="Active",
        )
        self.incident = IncidentReport.objects.create(
            user=self.tourist_user,
            incident_type="scam",
            description="Fake guide approach near the jetty.",
            location_label="Marine Jetty",
            latitude=9.9665,
            longitude=76.2420,
            risk_score_snapshot=74,
            status="reported",
        )
        RiskZone.objects.create(
            latitude=9.9670,
            longitude=76.2450,
            risk_type="crime",
            risk_score=81,
            description="Late-night assault cluster",
            city="Ernakulam",
        )
        SafeHaven.objects.create(
            name="City Police Control Room",
            type="police",
            latitude=9.9690,
            longitude=76.2440,
            address="MG Road, Kochi",
            phone="+91-7777777777",
            is_open_24_7=True,
        )
        self.client.force_login(self.admin_user)

    def test_admin_dashboard_page_renders_live_module(self):
        response = self.client.get(reverse("admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin Dashboard")
        self.assertContains(response, "Recent Alerts")

    def test_admin_dashboard_api_returns_live_payload(self):
        response = self.client.get(reverse("api_admin_dashboard_data"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertGreaterEqual(payload["summary"]["total_users"], 3)
        self.assertGreaterEqual(len(payload["recent_alerts"]), 1)

    def test_admin_users_page_can_suspend_and_reactivate_user(self):
        suspend_response = self.client.post(reverse("admin_users"), {"user_id": self.tourist_user.id, "action": "suspend"})
        self.assertEqual(suspend_response.status_code, 302)
        self.tourist_user.refresh_from_db()
        self.assertFalse(self.tourist_user.is_active)

        activate_response = self.client.post(reverse("admin_users"), {"user_id": self.tourist_user.id, "action": "activate"})
        self.assertEqual(activate_response.status_code, 302)
        self.tourist_user.refresh_from_db()
        self.assertTrue(self.tourist_user.is_active)

    def test_admin_sos_alerts_page_can_update_status(self):
        response = self.client.post(reverse("admin_sos_alerts"), {"alert_id": self.alert.id, "status": "Resolved"})

        self.assertEqual(response.status_code, 302)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, "Resolved")

    def test_admin_incidents_page_can_update_status(self):
        response = self.client.post(reverse("admin_incidents"), {"incident_id": self.incident.id, "status": "reviewing"})

        self.assertEqual(response.status_code, 302)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, "reviewing")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="alerts@safepassage.test",
        EMAIL_HOST_USER="alerts@safepassage.test",
    )
    def test_admin_notifications_broadcast_sends_individual_emails(self):
        response = self.client.post(
            reverse("admin_notifications"),
            {
                "audience": "all",
                "subject": "Safety Notice",
                "message": "Stay alert near MG Road tonight.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Broadcast delivered to 3 user(s)")
        self.assertEqual(len(mail.outbox), 3)
        self.assertTrue(all(message.from_email == "alerts@safepassage.test" for message in mail.outbox))
        self.assertEqual(
            sorted(message.to[0] for message in mail.outbox),
            sorted(
                [
                    self.admin_user.email,
                    self.tourist_user.email,
                    self.worker_user.email,
                ]
            ),
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="smtp.gmail.com",
        EMAIL_PORT=587,
        EMAIL_USE_TLS=True,
        EMAIL_HOST_USER="",
        EMAIL_HOST_PASSWORD="",
        DEFAULT_FROM_EMAIL="",
    )
    def test_admin_notifications_rejects_missing_smtp_configuration(self):
        response = self.client.post(
            reverse("admin_notifications"),
            {
                "audience": "all",
                "subject": "Safety Notice",
                "message": "Stay alert near MG Road tonight.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SMTP notification delivery is not configured correctly yet.")
