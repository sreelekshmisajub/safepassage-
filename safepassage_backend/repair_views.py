import os

views_path = r'c:\Users\ADMIN\Downloads\critical\sreethika\safepassage\safepassage_backend\safety\views.py'

with open(views_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the start of the section to replace
start_idx = -1
for i, line in enumerate(lines):
    if '# 🔐 Login Page' in line:
        start_idx = i
        break

# Find the start of Cultural Guide (which marks the end of our target section)
end_idx = -1
for i, line in enumerate(lines):
    if '# 🌍 Cultural Guide' in line:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    new_section = [
        '# 🔐 Login Page\n',
        'def user_login(request):\n',
        '    if request.method == "POST":\n',
        '        email = request.POST.get("email")\n',
        '        password = request.POST.get("password")\n',
        '        role = request.POST.get("role")\n',
        '\n',
        '        user = authenticate(request, username=email, password=password)\n',
        '\n',
        '        if user is not None:\n',
        '            if user.role == role:\n',
        '                login(request, user)\n',
        '                # Redirection based on role\n',
        '                if user.role == "tourist":\n',
        '                    return redirect("/dashboard/?mode=tourist")\n',
        '                elif user.role == "worker":\n',
        '                    return redirect("worker_dashboard")\n',
        '                elif user.role == "employer":\n',
        '                    return redirect("employer_dashboard")\n',
        '                elif user.role == "admin" or user.is_superuser:\n',
        '                    return redirect("admin_dashboard")\n',
        '            else:\n',
        '                messages.error(request, f"Incorrect role selected for this account.")\n',
        '        else:\n',
        '            messages.error(request, "Invalid email or password.")\n',
        '\n',
        '    return render(request, "login.html")\n',
        '\n',
        '\n',
        '# 🚶 Logout\n',
        'def user_logout(request):\n',
        '    logout(request)\n',
        '    return redirect("index")\n',
        '\n',
        '\n',
        '# 🗺️ Tourist Dashboard\n',
        '@login_required(login_url=\'login\')\n',
        'def tourist_dashboard(request):\n',
        '    if request.user.role != \'tourist\' and not request.user.is_superuser:\n',
        '        return render(request, "unauthorized.html")\n',
        '    return render(\n',
        '        request,\n',
        '        "tourist_dashboard.html",\n',
        '        {\n',
        '            "emergency_contacts_count": EmergencyContact.objects.filter(user=request.user).count(),\n',
        '        },\n',
        '    )\n',
        '\n',
        '\n',
        '@tourist_required\n',
        'def tourist_alerts(request):\n',
        '    return render(request, "tourist_alerts.html")\n',
        '\n',
        '\n',
        '@tourist_required\n',
        'def tourist_translate(request):\n',
        '    quick_phrases = ["Help", "Police", "Hospital", "Emergency", "Danger", "Lost", "Water", "Medicine", "Fire", "Stop"]\n',
        '    return render(request, "tourist_translate.html", {"quick_phrases": quick_phrases})\n',
        '\n',
        '\n',
        '# Tourist Emergency\n',
        '@tourist_required\n',
        'def tourist_emergency(request):\n',
        '    return render(\n',
        '        request,\n',
        '        "tourist_emergency.html",\n',
        '        {\n',
        '            "emergency_contacts_count": EmergencyContact.objects.filter(user=request.user).count(),\n',
        '        },\n',
        '    )\n',
        '\n',
        '\n',
        '@tourist_required\n',
        'def tourist_dashboard_hub(request):\n',
        '    requested_mode = request.GET.get("mode")\n',
        '    if requested_mode and requested_mode != "tourist":\n',
        '        return redirect("/dashboard/?mode=tourist")\n',
        '    return tourist_dashboard(request)\n',
        '\n',
        '\n',
        '# 📊 Tourist Risk Map\n',
        '@login_required(login_url=\'login\')\n',
        'def tourist_risk_map(request):\n',
        '    if request.user.role != \'tourist\' and not request.user.is_superuser:\n',
        '        return render(request, "unauthorized.html")\n',
        '    \n',
        '    risk_zones = RiskZone.objects.all()\n',
        '    return render(request, "tourist_risk_map.html", {\n',
        '        "risk_zones": risk_zones,\n',
        '        "initial_tab": "routes" if request.GET.get("tab") == "routes" else "live",\n',
        '    })\n',
        '\n',
        '\n',
        '@tourist_required\n',
        'def tourist_safe_route(request):\n',
        '    return redirect("/map/?tab=routes")\n',
        '\n',
        '\n',
        '@tourist_required\n',
        'def tourist_scam_alerts(request):\n',
        '    return render(request, "tourist_scam_alerts.html")\n',
        '\n',
        '\n',
        '@tourist_required\n',
        'def tourist_emergency_contacts(request):\n',
        '    return render(\n',
        '        request,\n',
        '        "tourist_emergency_contacts.html",\n',
        '        {\n',
        '            "emergency_contacts_count": EmergencyContact.objects.filter(user=request.user).count(),\n',
        '            "emergency_contacts": EmergencyContact.objects.filter(user=request.user).order_by("-is_primary", "name"),\n',
        '        },\n',
        '    )\n',
        '\n'
    ]
    
    # We replace from start_idx up to end_idx-1
    # But wait, we need to be careful if there are multiple "# 🌍 Cultural Guide"
    # Or multiple "# 🔐 Login Page"
    # In my case, I want to replace the WHOLE mess.
    
    # Actually, we find the LAST occurrence of tourist_emergency_contacts ending
    # but the end_idx logic is safer if Cultural Guide is unique.
    
    lines[start_idx:end_idx] = new_section
    
    with open(views_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print("Success: views.py repaired.")
else:
    print(f"Error: Could not find markers. start={start_idx}, end={end_idx}")
