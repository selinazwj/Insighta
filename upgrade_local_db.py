from app.database import engine

print('Using database:', engine.url)
print('Schema sync runs automatically on startup now.')
print('If you can read this without an exception, the database driver loaded correctly.')
print('Done.')
