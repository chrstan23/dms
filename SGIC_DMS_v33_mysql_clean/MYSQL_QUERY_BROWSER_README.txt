SGIC DMS - MySQL Query Browser (diagnostic tool)
================================================
START:  double-click start_mysql_query_browser.bat (installs the MySQL connector the first time).
DEFAULTS: host 192.168.0.202 | port 3306 | user sgic_app | database SGIC_DMS. The password is asked
          every time and is never saved. (3306 is the MySQL port - NOT the DMS web port 5000.)

ON THE SERVER PC (192.168.0.202): works as it is.
FROM ANOTHER PC ON THE LAN: copy this folder there and run the same .bat, but the MySQL server must allow it:
  1) MySQL account that may log in from the LAN (run once in MySQL Workbench as root, change the password):
       CREATE USER 'sgic_view'@'192.168.0.%' IDENTIFIED BY 'CHANGE_ME';
       GRANT SELECT ON SGIC_DMS.* TO 'sgic_view'@'192.168.0.%';
     Then connect with user sgic_view (read-only). Do not use root over the network.
  2) MySQL must listen on the network: in my.ini set  bind-address = 0.0.0.0  (default is often 127.0.0.1),
     then restart the MySQL service.
  3) Windows Firewall on the server (as Administrator), LAN only:
       netsh advfirewall firewall add rule name="MySQL 3306 LAN" dir=in action=allow protocol=TCP localport=3306 profile=any remoteip=localsubnet
  Never expose port 3306 to the internet.

FEATURES: browse databases/tables, click a table for SELECT ... LIMIT 100, run SQL, quick actions for users/grants/
version. Write statements are OFF by default and need explicit enabling + confirmation.
NOTE: this is not the original Oracle MySQL Query Browser. DMS itself already uses MySQL (database.ini).
