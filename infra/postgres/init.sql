-- Enable uuid-ossp extension for generic UUID support
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Verify database initialization
SELECT 'Database initialized successfully' as status;
