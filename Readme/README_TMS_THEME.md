# Calitrans TMS Theme Upgrade

Included:
- app_tms_replacement.py
- theme.css
- assets/calitrans_logo.png

How to install:
1. Copy theme.css into your project folder next to app.py.
2. Copy the assets folder into your project folder.
3. Backup your current app.py.
4. Replace app.py with the contents of app_tms_replacement.py.
5. Commit and push.

Commands:
git add app.py theme.css assets/calitrans_logo.png
git commit -m "Upgrade TMS dashboard theme"
git push origin master