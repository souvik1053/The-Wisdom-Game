CREATE DATABASE IF NOT EXISTS trader_quest
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE trader_quest;

CREATE TABLE IF NOT EXISTS books (
    book_id INT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    author VARCHAR(255),
    category VARCHAR(255),
    total_pages DECIMAL(10,2) DEFAULT 0,
    start_date DATE,
    status VARCHAR(50) DEFAULT 'Not Started',
    finish_date DATE,
    pages_read DECIMAL(10,2) DEFAULT 0
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS levels (
    level INT PRIMARY KEY,
    xp DECIMAL(12,2) DEFAULT 0,
    title VARCHAR(255)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS monthly_goals (
    target_month DATE PRIMARY KEY,
    books_goal DECIMAL(10,2) DEFAULT 0,
    pages_goal DECIMAL(10,2) DEFAULT 0,
    xp_goal DECIMAL(10,2) DEFAULT 0
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS reading_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    book VARCHAR(255),
    category VARCHAR(255),
    pages_from DECIMAL(10,2) DEFAULT 0,
    pages_read DECIMAL(10,2) DEFAULT 0,
    session_type VARCHAR(50) DEFAULT 'Normal',
    notes TEXT,
    INDEX idx_reading_log_date (date),
    INDEX idx_reading_log_book (book)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS settings (
    `key` VARCHAR(255) PRIMARY KEY,
    `value` TEXT
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS xp_rules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    action VARCHAR(255),
    xp DECIMAL(12,2)
) ENGINE=InnoDB;
