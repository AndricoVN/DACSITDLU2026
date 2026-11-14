-- Structure only. SQL Server 2022; run from the repository root in sqlcmd mode.
-- Example (Windows integrated authentication, no password in this file):
-- sqlcmd -S localhost -E -d master -b -i dwh/schema_v2.sql
-- For SQL authentication supply credentials securely outside this script.
-- -b is REQUIRED for a nonzero process exit on SQL errors; :On Error exit
-- also stops later GO batches. Do not run selected batches or strip directives.
-- Existing target is an error, including a partially created target from a failed run.
-- Investigate failures manually; this script never repairs/reuses an existing DB.
:On Error exit

USE [master];
GO
IF DB_NAME() <> N'master' OR DB_ID(N'lms_datawarehouse_v2') IS NOT NULL
BEGIN
    THROW 51000, 'Expected master and a nonexistent lms_datawarehouse_v2; refusing to continue.', 1;
END;
GO
CREATE DATABASE [lms_datawarehouse_v2];
GO
USE [lms_datawarehouse_v2];
GO
-- Guard applies to ALL table/index DDL in this batch. THROW must execute:
-- do not enable NOEXEC before it. sqlcmd :On Error exit stops later batches;
-- the required -b flag reports SQL errors as a nonzero process exit.
IF DB_NAME() <> N'lms_datawarehouse_v2'
BEGIN
    THROW 51001, 'Wrong database: expected lms_datawarehouse_v2.', 1;
END;

-- Populated only by a future validated import. No manifest or coverage is seeded.
-- SourceSystem identifies the curated source, not an unverified Moodle shell.
-- ManifestSha256 identifies the immutable input manifest; no secrets/raw PII here.
CREATE TABLE dbo.Source_Import (
    SourceImportKey BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    SourceSystem NVARCHAR(200) NOT NULL,
    SourceDatabase NVARCHAR(128) NULL,
    SourceSchemaVersion NVARCHAR(100) NULL,
    ManifestSha256 CHAR(64) NOT NULL,
    MappingVersion NVARCHAR(100) NOT NULL,
    SourceExtractedAt DATETIMEOFFSET(7) NULL,
    ImportedAt DATETIMEOFFSET(7) NOT NULL,
    SourceTimeZone NVARCHAR(100) NULL
);

-- Calendar is intentionally empty. ETL will populate actual required dates.
CREATE TABLE dbo.Dim_Date (
    DateKey INT NOT NULL PRIMARY KEY,
    FullDate DATE NOT NULL UNIQUE,
    DayOfWeek TINYINT NOT NULL,
    DayName NVARCHAR(20) NOT NULL,
    DayOfMonth TINYINT NOT NULL,
    DayOfYear SMALLINT NOT NULL,
    WeekOfYear TINYINT NOT NULL,
    MonthName NVARCHAR(20) NOT NULL,
    MonthOfYear TINYINT NOT NULL,
    Quarter TINYINT NOT NULL,
    Year INT NOT NULL,
    IsWeekend BIT NOT NULL
);

-- Nullable source flags mean unknown, never implicitly active/eligible.
-- PasswordHash is reserved for the later verified authentication flow.
CREATE TABLE dbo.Dim_User (
    UserKey INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    SourceUserID BIGINT NOT NULL UNIQUE,
    Username NVARCHAR(100) NOT NULL,
    PasswordHash NVARCHAR(255) NULL,
    IdNumber NVARCHAR(100) NULL,
    StudentCode VARCHAR(7) NULL,
    FullName NVARCHAR(200) NOT NULL,
    Email NVARCHAR(254) NULL,
    Department NVARCHAR(255) NULL,
    Institution NVARCHAR(255) NULL,
    IsStudent BIT NULL,
    IsDeleted BIT NULL,
    IsSuspended BIT NULL,
    IsTechnicalAccount BIT NULL,
    CreatedAt DATETIMEOFFSET(7) NULL,
    SourceCreatedTime BIGINT NULL,
    SourceModifiedTime BIGINT NULL,
    SourceImportKey BIGINT NOT NULL,
    CONSTRAINT FK_DU_Import FOREIGN KEY (SourceImportKey) REFERENCES dbo.Source_Import(SourceImportKey)
);
CREATE INDEX IX_Dim_User_Username ON dbo.Dim_User(Username);

-- Coverage describes the exported/validated dataset, not whether a user has a grade.
-- NULL = not assessed; missing = known absent from export; partial/complete refer
-- to the validated source scope. Never infer coverage from an empty fact table.
-- Category/year stay NULL if the source category cannot be resolved.
CREATE TABLE dbo.Dim_Course (
    CourseKey INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    SourceCourseID BIGINT NOT NULL UNIQUE,
    CourseName NVARCHAR(1000) NOT NULL,
    CourseShortName NVARCHAR(255) NOT NULL,
    CourseCode NVARCHAR(100) NULL,
    SourceCategoryID BIGINT NULL,
    CategoryName NVARCHAR(255) NULL,
    CategoryPath NVARCHAR(2000) NULL,
    AcademicYear NVARCHAR(50) NULL,
    StartDate DATETIMEOFFSET(7) NULL,
    EndDate DATETIMEOFFSET(7) NULL,
    SourceStartTime BIGINT NULL,
    SourceEndTime BIGINT NULL,
    SourceVisible BIT NULL,
    OperationalVisible BIT NULL,
    GradeCoverage VARCHAR(16) NULL,
    AssignmentCoverage VARCHAR(16) NULL,
    ActivityMetadataCoverage VARCHAR(16) NULL,
    CoverageNote NVARCHAR(1000) NULL,
    SourceImportKey BIGINT NOT NULL,
    CONSTRAINT CK_DC_GradeCoverage CHECK (GradeCoverage IN ('missing', 'partial', 'complete')),
    CONSTRAINT CK_DC_AssignmentCoverage CHECK (AssignmentCoverage IN ('missing', 'partial', 'complete')),
    CONSTRAINT CK_DC_MetadataCoverage CHECK (ActivityMetadataCoverage IN ('missing', 'partial', 'complete')),
    CONSTRAINT FK_DC_Import FOREIGN KEY (SourceImportKey) REFERENCES dbo.Source_Import(SourceImportKey)
);

-- One historical/current aggregate per user-course, not one row per enrol method.
-- ETL must assess each method's status AND time window before aggregating:
-- HasActiveEnrolment is true if at least one verified method is currently valid;
-- it is NOT a complete activity access decision. NULL = cannot determine.
-- Raw per-method records remain in source landing; do not collapse differing
-- method windows into a fabricated single start/end or status here.
CREATE TABLE dbo.Fact_User_Enrolments (
    EnrolmentKey BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    UserKey INT NOT NULL,
    CourseKey INT NOT NULL,
    SourceEnrolmentCount INT NULL,
    ActiveEnrolmentCount INT NULL,
    HasActiveEnrolment BIT NULL,
    AccessEvaluatedAt DATETIMEOFFSET(7) NULL,
    SourceImportKey BIGINT NOT NULL,
    CONSTRAINT UQ_FUE_UserCourse UNIQUE (UserKey, CourseKey),
    CONSTRAINT CK_FUE_Counts CHECK (
        (SourceEnrolmentCount IS NULL OR SourceEnrolmentCount > 0)
        AND (ActiveEnrolmentCount IS NULL OR ActiveEnrolmentCount >= 0)
        AND (SourceEnrolmentCount IS NULL OR ActiveEnrolmentCount IS NULL
             OR ActiveEnrolmentCount <= SourceEnrolmentCount)),
    CONSTRAINT FK_FUE_User FOREIGN KEY (UserKey) REFERENCES dbo.Dim_User(UserKey),
    CONSTRAINT FK_FUE_Course FOREIGN KEY (CourseKey) REFERENCES dbo.Dim_Course(CourseKey),
    CONSTRAINT FK_FUE_Import FOREIGN KEY (SourceImportKey) REFERENCES dbo.Source_Import(SourceImportKey)
);
CREATE INDEX IX_FUE_Course ON dbo.Fact_User_Enrolments(CourseKey);

-- Preserve module AND section metadata for phase 4 evaluation. Raw availability
-- is not constrained to valid JSON: malformed/unsupported data must stay unknown.
-- Missing metadata never means visible or allowed. No permission engine is built.
CREATE TABLE dbo.Dim_Assign (
    AssignKey INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    SourceAssignID BIGINT NOT NULL UNIQUE,
    CourseKey INT NOT NULL,
    AssignName NVARCHAR(1000) NOT NULL,
    AllowFromDate DATETIMEOFFSET(7) NULL,
    DueDate DATETIMEOFFSET(7) NULL,
    CutOffDate DATETIMEOFFSET(7) NULL,
    SourceAllowFromTime BIGINT NULL,
    SourceDueTime BIGINT NULL,
    SourceCutOffTime BIGINT NULL,
    SourceModifiedTime BIGINT NULL,
    MaxGrade DECIMAL(28,12) NULL,
    SourceGradeType INT NULL,
    SourceScaleID BIGINT NULL,
    SourceCourseModuleID BIGINT NULL,
    SourceSectionID BIGINT NULL,
    ModuleVisible BIT NULL,
    ModuleVisibleOnCoursePage BIT NULL,
    SectionVisible BIT NULL,
    ModuleAvailabilityRaw NVARCHAR(MAX) NULL,
    SectionAvailabilityRaw NVARCHAR(MAX) NULL,
    MetadataVerified BIT NULL,
    TeamSubmission BIT NULL,
    RequireAllTeamMembersSubmit BIT NULL,
    SourceGroupingID BIGINT NULL,
    SourceImportKey BIGINT NOT NULL,
    CONSTRAINT UQ_DA_AssignCourse UNIQUE (AssignKey, CourseKey),
    CONSTRAINT FK_DA_Course FOREIGN KEY (CourseKey) REFERENCES dbo.Dim_Course(CourseKey),
    CONSTRAINT FK_DA_Import FOREIGN KEY (SourceImportKey) REFERENCES dbo.Source_Import(SourceImportKey)
);

-- One current course-total result per user-course, not individual grade items.
-- Preserve invalid/negative raw values for diagnostics. Validated/scaled values
-- and classifications remain NULL until ETL validates numeric scale and result.
-- Classify on unrounded scaled precision, not display-rounded values.
CREATE TABLE dbo.Fact_Course_Grades (
    CourseGradeKey BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    UserKey INT NOT NULL,
    CourseKey INT NOT NULL,
    DateKey INT NULL,
    SourceGradeItemID BIGINT NULL,
    SourceGradeGradeID BIGINT NULL,
    SourceGradeType INT NULL,
    SourceScaleID BIGINT NULL,
    RawGrade DECIMAL(28,12) NULL,
    SourceRawGrade DECIMAL(28,12) NULL,
    SourceRawMinGrade DECIMAL(28,12) NULL,
    SourceRawMaxGrade DECIMAL(28,12) NULL,
    MinGrade DECIMAL(28,12) NULL,
    MaxGrade DECIMAL(28,12) NULL,
    GradeScaled10 DECIMAL(28,16) NULL,
    GradeValidationStatus NVARCHAR(50) NULL,
    LetterGrade VARCHAR(5) NULL,
    GradeClassification NVARCHAR(50) NULL,
    GradePoint4 DECIMAL(5,2) NULL,
    IsPassed BIT NULL,
    SourceHidden BIGINT NULL,
    SourceLocked BIGINT NULL,
    SourceCreatedTime BIGINT NULL,
    SourceModifiedTime BIGINT NULL,
    LastUpdated DATETIMEOFFSET(7) NULL,
    SourceImportKey BIGINT NOT NULL,
    CONSTRAINT UQ_FCG_UserCourse UNIQUE (UserKey, CourseKey),
    CONSTRAINT FK_FCG_User FOREIGN KEY (UserKey) REFERENCES dbo.Dim_User(UserKey),
    CONSTRAINT FK_FCG_Course FOREIGN KEY (CourseKey) REFERENCES dbo.Dim_Course(CourseKey),
    CONSTRAINT FK_FCG_Date FOREIGN KEY (DateKey) REFERENCES dbo.Dim_Date(DateKey),
    CONSTRAINT FK_FCG_Import FOREIGN KEY (SourceImportKey) REFERENCES dbo.Source_Import(SourceImportKey)
);

-- One latest result per real user-assignment. No synthetic user for group userid=0.
-- Grade must match SubmissionAttemptNumber; do not carry a prior attempt's grade.
-- Unknown group membership/result remains NULL and is excluded from late/missing
-- submission KPIs. A missing source submission is not proof of non-submission.
-- Only source status 'submitted' counts as submitted; preserve other raw statuses.
CREATE TABLE dbo.Fact_Assign_Submissions (
    SubmissionKey BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    SourceSubmissionID BIGINT NULL,
    UserKey INT NOT NULL,
    AssignKey INT NOT NULL,
    CourseKey INT NOT NULL,
    DateKey INT NULL,
    SubmissionStatus NVARCHAR(50) NULL,
    SubmissionAttemptNumber INT NULL,
    SourceLatest BIT NULL,
    SourceSubmissionUserID BIGINT NULL,
    SourceGroupID BIGINT NULL,
    TeamResultKnown BIT NULL,
    SourceAssignGradeID BIGINT NULL,
    GradeAttemptNumber INT NULL,
    Grade DECIMAL(28,12) NULL,
    GradeScaled10 DECIMAL(28,16) NULL,
    GradeValidationStatus NVARCHAR(50) NULL,
    IsPassed BIT NULL,
    IsLate BIT NULL,
    DaysLate INT NULL,
    SubmissionTime DATETIMEOFFSET(7) NULL,
    DueDate DATETIMEOFFSET(7) NULL,
    SourceSubmissionCreatedTime BIGINT NULL,
    SourceSubmissionModifiedTime BIGINT NULL,
    SourceGradeCreatedTime BIGINT NULL,
    SourceGradeModifiedTime BIGINT NULL,
    SourceImportKey BIGINT NOT NULL,
    CONSTRAINT UQ_FAS_UserAssign UNIQUE (UserKey, AssignKey),
    CONSTRAINT FK_FAS_User FOREIGN KEY (UserKey) REFERENCES dbo.Dim_User(UserKey),
    CONSTRAINT FK_FAS_AssignCourse FOREIGN KEY (AssignKey, CourseKey)
        REFERENCES dbo.Dim_Assign(AssignKey, CourseKey),
    CONSTRAINT FK_FAS_Course FOREIGN KEY (CourseKey) REFERENCES dbo.Dim_Course(CourseKey),
    CONSTRAINT FK_FAS_Date FOREIGN KEY (DateKey) REFERENCES dbo.Dim_Date(DateKey),
    CONSTRAINT FK_FAS_Import FOREIGN KEY (SourceImportKey) REFERENCES dbo.Source_Import(SourceImportKey)
);
CREATE INDEX IX_FAS_UserCourse ON dbo.Fact_Assign_Submissions(UserKey, CourseKey);
GO
-- No INSERT/MERGE, date seed, ETL, or source connection. All tables remain empty.
-- Normalized times use explicit offsets; raw BIGINT fields retain source Unix
-- seconds (including zero sentinels). Future ETL must map absent dates to NULL,
-- preserve raw values, and render actual times in Asia/Ho_Chi_Minh without shifting
-- historical deadlines. No CURRENT_TIMESTAMP or business-value defaults.
