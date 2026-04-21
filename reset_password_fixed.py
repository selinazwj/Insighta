from passlib.context import CryptContext
import sqlite3
import os

DB = 'survey.db'
DEFAULT_EMAIL = 'g1013360438@gmail.com'
DEFAULT_PASSWORD = 'Test@123456'

pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')

email = input(f'Email [{DEFAULT_EMAIL}]: ').strip() or DEFAULT_EMAIL
new_password = input(f'New password [{DEFAULT_PASSWORD}]: ').strip() or DEFAULT_PASSWORD

print('Using DB:', os.path.abspath(DB))
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute('SELECT id, email FROM users WHERE lower(email)=lower(?)', (email,))
row = cur.fetchone()
if not row:
    print('User not found:', email)
else:
    hashed = pwd_context.hash(new_password)
    cur.execute('UPDATE users SET password=? WHERE id=?', (hashed, row[0]))
    conn.commit()
    print('Password reset success')
    print('User:', row[1])
    print('New password:', new_password)
conn.close()
