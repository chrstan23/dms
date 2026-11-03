SGIC DOCUMENT MANAGEMENT SYSTEM - SETUP & DEPLOYMENT GUIDE
==========================================================

1. WHAT YOU NEED
   - One Windows PC/server that stays on (the "host"). Install Python 3.9+ from python.org
     (tick "Add Python to PATH").
   - Put this whole folder on the host's LOCAL disk, ideally  C:\SGIC_DMS
     (NOT on a network share: the SQLite database can be damaged there, and long network paths
     break long document names).
   - Employee PCs only need a web browser.

2. INSTALL (host, once)
   a) install_server.bat      - creates the private Python environment and installs Flask + Waitress
   b) check_install.bat       - self-check; fix every FAIL line, read every WARN line
   c) start_server.bat        - starts the system. The window shows the address employees must use,
                                for example  http://192.168.0.50:5000   (keep the window open)
   Optional, run as Administrator:
   d) allow_lan_port_5000.bat - (run as Administrator) opens TCP 5000 for the LAN on all network profiles. REQUIRED for other PCs.
   e) install_autostart.bat   - start automatically when the PC boots (remove: remove_autostart.bat)
   f) schedule_backup.bat     - daily backup at 18:00 (newest 30 kept in the backups folder)
   Ask IT to give the host a fixed IP address (DHCP reservation) so the address never changes.

3. FIRST LOGIN AND ACCOUNTS
   The first start creates demo accounts. EACH ONE MUST SET A NEW PASSWORD AT ITS FIRST LOGIN:
      admin / admin123   (Administrator)      dcc / dcc123     (DCC - Document Control Center)
      author / author123 (Author)             requestor / requestor123 (Requestor)
      admr / admr123     (ADMR - Assistant Department Manager)
      agmr / agmr123     (AGMR - Assistant General Manager, final approval)
   - Log in as admin, open Manage Users, create real accounts (name, section, username, role) and
     remove the demo accounts you do not need. New accounts also have to change their password at first login.
   - Reset a forgotten password: Manage Users > Reset password (a temporary password is shown once).
   - Everyone can change their own password with "Change Password" at the top right.
   - Sessions end after 8 hours without use; 5 wrong passwords lock that login for 10 minutes.

4. HOW THE WORKFLOW WORKS
   NEW DOCUMENT:  Requestor creates the request -> DCC accepts and assigns the Document No. -> Requestor submits
                  the PDF -> ADMR approves -> AGMR gives the final approval (may attach the approved PDF) or rejects
                  with a comment -> the Requestor sends ALL the editable Word / Excel files -> Completed.
   REVISION:      Same chain, but first DCC releases the editable files to the requestor. DCC can attach SEVERAL
                  files in ONE release (main procedure, mother procedure, forms...), Word (.docx/.doc) or Excel
                  (.xlsx/.xls), and label each one. Everything for one revision stays in ONE request.
   Rejected requests go back to the requestor, who resubmits; the chain starts again at ADMR.
   After the final approval the document is already Approved with its new PDF; the request stays open as
   "Waiting for Editable Files" until the requestor sends them (DCC can close it with a comment if there are none).
   AGMR sees only Dashboard, Revision Requests and Document Master List.

5. WHERE THINGS ARE SAVED (inside the program folder)
   sgic_dms.db                          the SQLite database (kept as migration backup when MySQL is selected)
   pdf_documents\<Type>\<Department>\   controlled PDFs named <DocNo>_<Title>_<Revision>.pdf (old revisions in \archive)
   uploads\                             request attachments (the released Word/Excel files and the submitted PDFs, each saved under its original file name)
   pdf_documents\<Type>\<Department>\Editable\<DocNo>\   the final editable Word/Excel files (same names; older sets in \archive)
   backups\                             backup ZIPs          logs\dms.log   server log (rotates automatically)
   background.png                       login/background picture (replace to change it)
   Downloading a PDF from the viewer needs the download password. To change it, set the environment
   variable SGIC_PDF_PASSWORD before starting the server.

6. BACKUP AND RESTORE
   - backup_now.bat makes a backup immediately; schedule_backup.bat does it daily.
   - Copy the backups folder to another PC or drive regularly (a backup on the same disk is not enough).
   - Restore: stop the server and restore the matching database backup and the uploads/pdf_documents folders.
     SQLite backups contain sgic_dms.db; SQL Server backups contain a .bak; MySQL backups contain a .sql dump.
     MySQL restore is performed with the MySQL client (for example, mysql SGIC_DMS < backup.sql).

7. UPDATING TO A NEW VERSION
   Stop the server, run backup_now.bat, copy the new app.py (and any other changed files) over the old ones,
   start the server. Never delete sgic_dms.db, uploads or pdf_documents. The database upgrades itself.

8. TROUBLESHOOTING
   - Page does not open from another PC: run allow_lan_port_5000.bat as Administrator on the host; use the IPv4 address printed in the server window (never 0.0.0.0 or localhost); both PCs must be on the same network/VLAN.
   - "Port already in use": the server is already running, or set SGIC_PORT to another number.
   - "Waitress is not installed": run install_server.bat.
   - PDF cannot be saved / path too long: move the program to C:\SGIC_DMS.
   - The server log is logs\dms.log; health check: http://127.0.0.1:5000/healthz
   - Reset a password when nobody can log in: change_password.bat (on the host).

9. DATABASE: SQLITE (DEFAULT), SQL SERVER, OR MYSQL
   SQLite remains the default. MySQL mode is enabled only when database.ini says engine = mysql.
   To migrate the existing SQLite records safely to MySQL:
   a) Stop the SGIC DMS server (close its server window), back up the entire program folder, and keep uploads/ and pdf_documents/.
   b) Install MySQL Server 8.x (or a compatible MariaDB version validated by your IT team) and make sure the service is running on port 3306.
   c) In MySQL Workbench or the MySQL command line, run setup_mysql.sql as an administrator. CHANGE its example password first.
   d) Run install_server.bat if not already installed, then install_mysql_support.bat.
   e) Edit database.ini: engine = mysql; host = localhost; port = 3306; database = SGIC_DMS;
      mysql_user = sgic_app; mysql_password = the exact password you configured in setup_mysql.sql.
   f) IMPORTANT: do not start SGIC DMS in MySQL mode before migrating; first start seeds demo users into an empty database.
      Run migrate_to_mysql.bat. It creates a consistent SQLite backup in backups/, refuses a non-empty target,
      copies the five application tables, and verifies row counts. It never deletes or changes sgic_dms.db.
   g) Run check_mysql.bat. Confirm all table counts are correct, then start_server.bat and test login, documents,
      revision requests, file uploads/downloads, and the audit trail. Do not delete the SQLite file or backup until verified.
   h) The app account needs privileges on SGIC_DMS only. Keep database.ini private because it contains the password.


10. SECURITY NOTES
   - The system uses plain HTTP inside the company network. Do not expose port 5000 to the internet.
   - Keep the host PC patched and restrict who can log in to it. Back up .secret_key with the database
     (it keeps logins valid after a restore; if lost, everyone simply logs in again).
