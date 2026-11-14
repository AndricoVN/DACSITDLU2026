"""Static contract checks only: no SQL driver, network, ETL, or database execution.

Run from root: python -B dwh/test_schema_v2.py
These checks are not a substitute for a later SQL Server DDL smoke test.
"""
from pathlib import Path
import re
import unittest


SCHEMA = Path(__file__).with_name("schema_v2.sql").read_text(encoding="utf-8")
SQL = re.sub(r"--[^\n]*", "", SCHEMA)
BATCHES = [s.strip() for s in re.split(r"(?im)^GO\s*$", SQL) if s.strip()]
TABLES = dict(re.findall(
    r"CREATE TABLE dbo\.(\w+)\s*\((.*?)\n\);", SQL, re.S | re.I
))


class SchemaV2ContractTests(unittest.TestCase):
    def test_target_is_new_database_only(self):
        self.assertEqual(re.findall(r"CREATE DATABASE \[([^\]]+)\]", SQL),
                         ["lms_datawarehouse_v2"])
        self.assertEqual(re.findall(r"USE \[([^\]]+)\]", SQL),
                         ["master", "lms_datawarehouse_v2"])
        self.assertNotRegex(SQL, r"(?i)\b(?:DROP|TRUNCATE|DELETE|INSERT|MERGE|UPDATE|EXEC(?:UTE)?)\b")
        self.assertNotRegex(SQL, r"(?i)\bDEFAULT\b")
        self.assertNotRegex(SCHEMA, r"(?i)(?:password\s*=|\s-P\s+)")

    def test_sqlcmd_guard_structure_only(self):
        # Static regression check, NOT proof of runtime exit codes. A later
        # SQL Server/sqlcmd smoke test must verify existing-target failure.
        self.assertNotRegex(SQL, r"(?i)\bSET\s+NOEXEC\b")
        self.assertIn(":On Error exit", BATCHES[0])
        self.assertIn("-b", SCHEMA)
        self.assertIn("-d master", SCHEMA)
        self.assertEqual(BATCHES[1], (
            "IF DB_NAME() <> N'master' OR DB_ID(N'lms_datawarehouse_v2') IS NOT NULL\n"
            "BEGIN\n"
            "    THROW 51000, 'Expected master and a nonexistent lms_datawarehouse_v2; refusing to continue.', 1;\n"
            "END;"
        ))
        self.assertEqual(BATCHES[2], "CREATE DATABASE [lms_datawarehouse_v2];")
        self.assertEqual(BATCHES[3], "USE [lms_datawarehouse_v2];")
        ddl_batches = [b for b in BATCHES if re.search(r"CREATE TABLE", b)]
        self.assertEqual(len(ddl_batches), 1)
        guard = ddl_batches[0].split("CREATE TABLE", 1)[0]
        self.assertEqual(guard.strip(), (
            "IF DB_NAME() <> N'lms_datawarehouse_v2'\n"
            "BEGIN\n"
            "    THROW 51001, 'Wrong database: expected lms_datawarehouse_v2.', 1;\n"
            "END;"
        ))

    def test_scope_and_empty_calendar(self):
        self.assertEqual(set(TABLES), {
            "Source_Import", "Dim_Date", "Dim_User", "Dim_Course", "Dim_Assign",
            "Fact_User_Enrolments", "Fact_Course_Grades", "Fact_Assign_Submissions",
        })
        self.assertNotRegex(SQL, r"(?i)quiz|cohort|question|\bstag_")
        self.assertNotRegex(SQL, r"(?i)\b(?:INSERT|BULK|MERGE|SELECT\s+.*INTO)\b")

    def test_unique_grains_and_assignment_course_consistency(self):
        for name in ("Fact_User_Enrolments", "Fact_Course_Grades"):
            self.assertIn("UNIQUE (UserKey, CourseKey)", TABLES[name])
        self.assertIn("UNIQUE (UserKey, AssignKey)", TABLES["Fact_Assign_Submissions"])
        self.assertIn("UNIQUE (AssignKey, CourseKey)", TABLES["Dim_Assign"])
        self.assertIn("FOREIGN KEY (AssignKey, CourseKey)", TABLES["Fact_Assign_Submissions"])
        self.assertIn("REFERENCES dbo.Dim_Assign(AssignKey, CourseKey)", SQL)

    def test_precision_and_unknown_results(self):
        for name, raw, scaled in (
            ("Fact_Course_Grades", "RawGrade", "GradeScaled10"),
            ("Fact_Assign_Submissions", "Grade", "GradeScaled10"),
        ):
            self.assertIn(f"{raw} DECIMAL(28,12) NULL", TABLES[name])
            self.assertIn(f"{scaled} DECIMAL(28,16) NULL", TABLES[name])
            self.assertIn("IsPassed BIT NULL", TABLES[name])
        self.assertIn("LetterGrade VARCHAR(5) NULL", TABLES["Fact_Course_Grades"])
        self.assertIn("MaxGrade DECIMAL(28,12) NULL", TABLES["Dim_Assign"])
        self.assertIn("SubmissionStatus NVARCHAR(50) NULL", TABLES["Fact_Assign_Submissions"])
        self.assertIn("IsLate BIT NULL", TABLES["Fact_Assign_Submissions"])

    def test_source_coverage_and_phase4_metadata(self):
        for name, body in TABLES.items():
            if name not in ("Source_Import", "Dim_Date"):
                self.assertIn("SourceImportKey BIGINT NOT NULL", body)
                self.assertIn("REFERENCES dbo.Source_Import(SourceImportKey)", body)
        for field in ("GradeCoverage", "AssignmentCoverage", "ActivityMetadataCoverage",
                      "SourceCategoryID", "AcademicYear", "SourceVisible", "OperationalVisible"):
            self.assertIn(field, TABLES["Dim_Course"])
        for field in ("ModuleAvailabilityRaw", "SectionAvailabilityRaw", "ModuleVisible",
                      "SectionVisible", "SourceDueTime", "TeamSubmission", "SourceGroupingID"):
            self.assertIn(field, TABLES["Dim_Assign"])
        for field in ("SourceGroupID", "TeamResultKnown", "SubmissionAttemptNumber",
                      "GradeAttemptNumber", "SourceSubmissionModifiedTime"):
            self.assertIn(field, TABLES["Fact_Assign_Submissions"])
        for field in ("IsDeleted", "IsSuspended", "IsTechnicalAccount"):
            self.assertIn(f"{field} BIT NULL", TABLES["Dim_User"])
        self.assertIn("HasActiveEnrolment BIT NULL", TABLES["Fact_User_Enrolments"])

    def test_all_foreign_keys_reference_declared_columns(self):
        for body in TABLES.values():
            for target, columns in re.findall(r"REFERENCES dbo\.(\w+)\(([^)]+)\)", body):
                self.assertIn(target, TABLES)
                for column in columns.split(","):
                    self.assertRegex(TABLES[target], rf"(?m)^\s*{re.escape(column.strip())}\s+")


if __name__ == "__main__":
    unittest.main(verbosity=2)
