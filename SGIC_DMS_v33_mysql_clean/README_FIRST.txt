SGIC DMS - START HERE
=====================
HOST PC (once):   1) install_server.bat   2) check_install.bat   3) start_server.bat
EMPLOYEE PCs:     open the address shown in the server window (http://<host-ip>:5000) in a browser,
                  or run create_desktop_shortcut.bat for a desktop icon.
EVERY DAY:        nothing - optional: install_autostart.bat (starts with the PC) and
                  schedule_backup.bat (daily backup).
Full instructions: README_SETUP.txt

MYSQL OPTION: See README_SETUP.txt section 9. Configure database.ini, run install_mysql_support.bat,
then migrate_to_mysql.bat BEFORE starting SGIC DMS in MySQL mode. The migration preserves the SQLite source.
