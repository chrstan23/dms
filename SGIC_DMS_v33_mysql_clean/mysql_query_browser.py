"""Local MySQL diagnostic browser, inspired by the classic MySQL Query Browser UI.

Requires: pip install mysql-connector-python
Default connection: sgic_app@192.168.0.202:3306, schema SGIC_DMS. Password is requested at runtime.
"""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import mysql.connector
from mysql.connector import Error

APP_TITLE = "MySQL Query Browser — SGIC DMS Diagnostics"

class Browser(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry('1180x760')
        self.minsize(900, 580)
        self.conn = None
        self._build()

    def _build(self):
        connbox = ttk.LabelFrame(self, text='Connection')
        connbox.pack(fill='x', padx=8, pady=6)
        self.host = tk.StringVar(value='192.168.0.202')
        self.port = tk.StringVar(value='3306')
        self.user = tk.StringVar(value='sgic_app')
        self.db = tk.StringVar(value='SGIC_DMS')
        for col, label, var, width in [(0,'Host',self.host,18),(1,'Port',self.port,7),(2,'User',self.user,14),(3,'Default database',self.db,18)]:
            ttk.Label(connbox, text=label).grid(row=0,column=col*2,padx=(8,3),pady=7,sticky='w')
            ttk.Entry(connbox,textvariable=var,width=width).grid(row=0,column=col*2+1,padx=(0,8),pady=7,sticky='w')
        ttk.Button(connbox,text='Connect',command=self.connect).grid(row=0,column=8,padx=4)
        ttk.Button(connbox,text='Disconnect',command=self.disconnect).grid(row=0,column=9,padx=4)
        self.status = tk.StringVar(value='Not connected')
        ttk.Label(connbox,textvariable=self.status).grid(row=1,column=0,columnspan=10,padx=8,pady=(0,6),sticky='w')

        split = ttk.Panedwindow(self, orient='horizontal')
        split.pack(fill='both',expand=True,padx=8,pady=(0,8))
        left = ttk.Frame(split,width=270); right = ttk.Frame(split)
        split.add(left,weight=1); split.add(right,weight=4)
        ttk.Label(left,text='Schemas / tables').pack(anchor='w',padx=5,pady=(5,2))
        treebox=ttk.Frame(left); treebox.pack(fill='both',expand=True,padx=4,pady=4)
        self.tree=ttk.Treeview(treebox,columns=('kind',),show='tree')
        ys=ttk.Scrollbar(treebox,orient='vertical',command=self.tree.yview); self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side='left',fill='both',expand=True); ys.pack(side='right',fill='y')
        self.tree.bind('<<TreeviewSelect>>',self.tree_select)
        ttk.Button(left,text='Refresh schemas',command=self.refresh_schemas).pack(fill='x',padx=4,pady=4)

        self.tabs=ttk.Notebook(right); self.tabs.pack(fill='both',expand=True)
        querytab=ttk.Frame(self.tabs); self.tabs.add(querytab,text='SQL Query')
        ttk.Label(querytab,text='SQL statement (Ctrl+Enter to run). SELECT, SHOW, DESCRIBE, EXPLAIN and other statements are supported.').pack(anchor='w',padx=6,pady=(6,2))
        self.sql=tk.Text(querytab,height=10,wrap='none',undo=True)
        self.sql.pack(fill='x',padx=6,pady=4)
        self.sql.insert('1.0','SHOW TABLES;\n')
        bar=ttk.Frame(querytab); bar.pack(fill='x',padx=6,pady=4)
        self.allow_write=tk.BooleanVar(value=False)
        ttk.Checkbutton(bar,text='Allow write / schema-changing queries',variable=self.allow_write).pack(side='left')
        ttk.Button(bar,text='Run query',command=self.run_query).pack(side='right',padx=3)
        ttk.Button(bar,text='Clear results',command=self.clear_results).pack(side='right',padx=3)
        self.sql.bind('<Control-Return>',lambda e:(self.run_query(), 'break'))
        self.results=ttk.Frame(querytab); self.results.pack(fill='both',expand=True,padx=6,pady=4)
        self.result_tree=ttk.Treeview(self.results,show='headings')
        rys=ttk.Scrollbar(self.results,orient='vertical',command=self.result_tree.yview)
        rxs=ttk.Scrollbar(self.results,orient='horizontal',command=self.result_tree.xview)
        self.result_tree.configure(yscrollcommand=rys.set,xscrollcommand=rxs.set)
        self.result_tree.grid(row=0,column=0,sticky='nsew'); rys.grid(row=0,column=1,sticky='ns'); rxs.grid(row=1,column=0,sticky='ew')
        self.results.rowconfigure(0,weight=1); self.results.columnconfigure(0,weight=1)

        usertab=ttk.Frame(self.tabs); self.tabs.add(usertab,text='Users & privileges')
        ttk.Label(usertab,text='DMS application accounts are separate from MySQL server accounts. Some queries require administrator privileges.').pack(anchor='w',padx=6,pady=6)
        ttk.Button(usertab,text='Show DMS users',command=lambda:self.quick_query('SELECT username, full_name, section, role, must_change FROM SGIC_DMS.users ORDER BY username')).pack(anchor='w',padx=6,pady=3)
        ttk.Button(usertab,text='Show MySQL server users (admin only)',command=lambda:self.quick_query('SELECT User, Host, plugin, account_locked, password_expired FROM mysql.user ORDER BY User, Host')).pack(anchor='w',padx=6,pady=3)
        ttk.Button(usertab,text='Show current grants',command=lambda:self.quick_query('SHOW GRANTS')).pack(anchor='w',padx=6,pady=3)
        ttk.Button(usertab,text='Show current connection',command=lambda:self.quick_query('SELECT USER() AS login_user, CURRENT_USER() AS privilege_user, DATABASE() AS current_database, VERSION() AS server_version')).pack(anchor='w',padx=6,pady=3)

        helptext=('Quick diagnostics\n\n'
          'SHOW DATABASES;\nSHOW TABLES FROM your_database;\nDESCRIBE your_table;\n'
          'SELECT * FROM your_table LIMIT 100;\nSHOW CREATE TABLE your_table;\n'
          'SHOW FULL PROCESSLIST;\nSHOW VARIABLES;\nSHOW STATUS;\n\n'
          'Default connection is the SGIC DMS application account/database. To inspect MySQL server accounts, connect with a suitably privileged administrator. '
          'Security: this tool connects with the privileges of the supplied MySQL account. '
          'The write-query option is off by default. Use root only on a trusted local machine; '
          'do not expose this browser or MySQL port 3306 to the internet. Passwords are not saved.')
        helpbox=ttk.Frame(self.tabs); self.tabs.add(helpbox,text='Help / diagnostics')
        ttk.Label(helpbox,text=helptext,justify='left').pack(anchor='nw',padx=12,pady=12)

    def connect(self):
        self.disconnect(silent=True)
        password=simpledialog.askstring('MySQL password','Password for %s@%s:%s' % (self.user.get(),self.host.get(),self.port.get()),show='*',parent=self)
        if password is None: return
        try:
            self.conn=mysql.connector.connect(host=self.host.get().strip(),port=int(self.port.get()),user=self.user.get().strip(),password=password,database=self.db.get().strip() or None,connection_timeout=5)
            cur=self.conn.cursor(); cur.execute('SELECT VERSION(), CURRENT_USER(), DATABASE()'); row=cur.fetchone(); cur.close()
            self.status.set('Connected | MySQL %s | account %s | database %s' % (row[0],row[1],row[2] or '(none)'))
            self.refresh_schemas()
        except Exception as e:
            self.conn=None; self.status.set('Connection failed')
            messagebox.showerror('MySQL connection failed',str(e)+'\n\nCheck that MySQL is running, port 3306 is reachable, and the account/password are correct.')

    def disconnect(self,silent=False):
        if self.conn:
            try: self.conn.close()
            except Exception: pass
        self.conn=None
        if hasattr(self,'status'): self.status.set('Not connected')
        if not silent and hasattr(self,'tree'): self.tree.delete(*self.tree.get_children())

    def require_conn(self):
        if not self.conn or not self.conn.is_connected():
            messagebox.showwarning('Not connected','Connect to MySQL first.'); return False
        return True

    def refresh_schemas(self):
        if not self.require_conn(): return
        self.tree.delete(*self.tree.get_children())
        try:
            cur=self.conn.cursor(); cur.execute('SHOW DATABASES'); dbs=[r[0] for r in cur.fetchall()]; cur.close()
            for db in dbs:
                node=self.tree.insert('', 'end', text=db, values=('database',), open=(db==self.db.get()))
                try:
                    c=self.conn.cursor(); c.execute('SHOW TABLES FROM `%s`' % db.replace('`','``')); tables=[r[0] for r in c.fetchall()]; c.close()
                    for table in tables: self.tree.insert(node,'end',text=table,values=('table',))
                except Exception: pass
        except Exception as e: messagebox.showerror('Schema refresh failed',str(e))

    def tree_select(self,event=None):
        sel=self.tree.selection()
        if not sel: return
        item=sel[0]; parent=self.tree.parent(item); name=self.tree.item(item,'text')
        if parent:
            db=self.tree.item(parent,'text')
            self.sql.delete('1.0','end'); self.sql.insert('1.0','SELECT * FROM `%s`.`%s` LIMIT 100;'%(db.replace('`','``'),name.replace('`','``')))
        else:
            self.db.set(name)
            self.sql.delete('1.0','end'); self.sql.insert('1.0','SHOW TABLES FROM `%s`;'%name.replace('`','``'))

    def quick_query(self,sql):
        self.sql.delete('1.0','end'); self.sql.insert('1.0',sql); self.tabs.select(0); self.run_query()

    def clear_results(self):
        self.result_tree.delete(*self.result_tree.get_children()); self.result_tree['columns']=()

    def run_query(self):
        if not self.require_conn(): return
        sql=self.sql.get('1.0','end').strip()
        if not sql: return
        first=sql.lstrip().split(None,1)[0].upper().rstrip(';')
        read_prefixes=('SELECT','SHOW','DESCRIBE','DESC','EXPLAIN','WITH','HELP')
        is_read=first in read_prefixes
        if not is_read and not self.allow_write.get():
            messagebox.showwarning('Write queries disabled','This statement may change data or schema. Enable “Allow write / schema-changing queries” to run it.'); return
        if not is_read and not messagebox.askyesno('Confirm query','Run this potentially destructive or data-changing SQL statement?\n\nReview the query carefully and ensure you have a backup.'):
            return
        try:
            cur=self.conn.cursor()
            cur.execute(sql)
            self.clear_results()
            if cur.with_rows:
                cols=[d[0] for d in cur.description]
                self.result_tree['columns']=tuple(str(i) for i in range(len(cols)))
                for i,col in enumerate(cols): self.result_tree.heading(str(i),text=col); self.result_tree.column(str(i),width=150,minwidth=70,stretch=True)
                for row in cur.fetchmany(5000): self.result_tree.insert('', 'end',values=[self._display(v) for v in row])
                self.status.set('Query successful | columns: %d | up to 5,000 rows displayed' % len(cols))
            else:
                self.conn.commit(); self.status.set('Query successful | affected rows: %s' % cur.rowcount)
                messagebox.showinfo('Query successful','Statement completed. Affected rows: %s' % cur.rowcount)
            cur.close()
        except Exception as e:
            try: self.conn.rollback()
            except Exception: pass
            self.status.set('SQL error — see details')
            messagebox.showerror('SQL error',str(e))

    @staticmethod
    def _display(v):
        if v is None: return 'NULL'
        if isinstance(v,bytes): return '<binary: %d bytes>'%len(v)
        return str(v)

if __name__=='__main__':
    Browser().mainloop()
