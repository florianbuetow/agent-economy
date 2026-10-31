-- Q-M2 accepted-bid pricing: the immutable pricing-rule version each job's terms were
-- established under. Written in the same transaction as the task contract; later
-- rule changes add a version and never reinterpret an existing job. Only the
-- contract-creating writers insert rows; nothing updates or deletes them.
CREATE TABLE board_task_pricing (
    task_id TEXT PRIMARY KEY NOT NULL
            CHECK (typeof(task_id) = 'text' AND length(CAST(task_id AS BLOB)) > 0),
    pricing_rule_version INTEGER NOT NULL
            CHECK (typeof(pricing_rule_version) = 'integer' AND pricing_rule_version = 1),
    FOREIGN KEY (task_id) REFERENCES board_tasks(task_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;
