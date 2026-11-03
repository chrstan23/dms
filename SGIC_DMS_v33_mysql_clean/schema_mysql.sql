/* SGIC DMS MySQL schema. Uses InnoDB and utf8. Safe to run repeatedly. */
CREATE TABLE IF NOT EXISTS users (
    username VARCHAR(100) NOT NULL PRIMARY KEY,
    password_hash VARCHAR(400) NOT NULL,
    role VARCHAR(30) NOT NULL,
    full_name VARCHAR(200) DEFAULT '',
    section VARCHAR(200) DEFAULT '',
    must_change INT DEFAULT 0
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(100) NOT NULL PRIMARY KEY,
    doc_no VARCHAR(200) NOT NULL,
    title LONGTEXT,
    department VARCHAR(200),
    revision VARCHAR(50),
    status VARCHAR(100),
    created_by VARCHAR(100),
    date_created VARCHAR(40),
    revision_history LONGTEXT,
    current_attachment LONGTEXT,
    document_type VARCHAR(200) DEFAULT '',
    UNIQUE KEY UX_documents_doc_no (doc_no)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE IF NOT EXISTS existing_procedures (
    id VARCHAR(100) NOT NULL PRIMARY KEY,
    doc_no VARCHAR(200) NOT NULL,
    title LONGTEXT,
    department VARCHAR(200),
    document_type VARCHAR(200),
    revision VARCHAR(50),
    effective_date VARCHAR(40),
    status VARCHAR(100),
    file_reference LONGTEXT,
    remarks LONGTEXT,
    registered_by VARCHAR(100),
    registered_at VARCHAR(40),
    revision_in_progress INT DEFAULT 0,
    last_revision_reason LONGTEXT,
    last_revision_released_by VARCHAR(100),
    last_revision_attachment LONGTEXT,
    UNIQUE KEY UX_existing_procedures_doc_no (doc_no)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE IF NOT EXISTS revision_requests (
    id VARCHAR(100) NOT NULL PRIMARY KEY,
    source_type VARCHAR(30),
    source_id VARCHAR(100),
    doc_no VARCHAR(200),
    title LONGTEXT,
    department VARCHAR(200),
    current_revision VARCHAR(50),
    proposed_revision VARCHAR(50),
    reason LONGTEXT,
    status VARCHAR(100),
    requested_by VARCHAR(100),
    requested_at VARCHAR(40),
    updated_at VARCHAR(40),
    attachment LONGTEXT,
    attachment_added_by VARCHAR(100),
    attachment_added_at VARCHAR(40),
    comments LONGTEXT,
    history LONGTEXT,
    request_type VARCHAR(30) DEFAULT 'revision',
    KEY IX_revision_requests_source (source_id),
    KEY IX_revision_requests_status (status)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE IF NOT EXISTS audit (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100),
    activity LONGTEXT,
    `timestamp` VARCHAR(40)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;
