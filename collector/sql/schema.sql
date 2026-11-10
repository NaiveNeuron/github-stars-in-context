CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id BIGINT NOT NULL UNIQUE,
    github_name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    links_parsed_at TEXT,
    h_index INTEGER NOT NULL DEFAULT 0,
    repositories_fetched BOOLEAN NOT NULL DEFAULT 0,
    followers_count INTEGER NOT NULL DEFAULT 0,
    following_count INTEGER NOT NULL DEFAULT 0,
    sponsors_count INTEGER NOT NULL DEFAULT 0,
    organizations_count INTEGER NOT NULL DEFAULT 0,
    company TEXT,
    location TEXT,
    bio TEXT,
    bio_length INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    repository_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    stars INTEGER NOT NULL CHECK (stars >= 0),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE (user_id, repository_id)
);

CREATE TABLE IF NOT EXISTS user_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    link_type TEXT DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE (user_id, url)
);

CREATE INDEX IF NOT EXISTS idx_user_github_id ON users(github_id);
CREATE INDEX IF NOT EXISTS idx_user_github_name ON users(github_name);
CREATE INDEX IF NOT EXISTS idx_users_need_fetch ON users(repositories_fetched);
CREATE INDEX IF NOT EXISTS idx_users_followers ON users(followers_count);
CREATE INDEX IF NOT EXISTS idx_users_h_index ON users(h_index);
CREATE INDEX IF NOT EXISTS idx_users_links_parsed ON users(links_parsed_at);
CREATE INDEX IF NOT EXISTS idx_repo_user ON repositories(user_id);
CREATE INDEX IF NOT EXISTS idx_user_links_user_id ON user_links(user_id);
CREATE INDEX IF NOT EXISTS idx_user_links_url ON user_links(url);