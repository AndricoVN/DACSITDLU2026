USE lms_datawarehouse
GO

CREATE TABLE stag_Dim_User (
    SourceUserID BIGINT NOT NULL,
    Username NVARCHAR(100),
    PasswordHash NVARCHAR(255),
    IdNumber NVARCHAR(100),
    FullName NVARCHAR(200),
    Email NVARCHAR(100),
    Department NVARCHAR(255),
    Institution NVARCHAR(255),
    CohortName NVARCHAR(254),
    IsStudent BIT,
    CreatedAt DATETIME
);

CREATE TABLE stag_Dim_Course (
    SourceCourseID BIGINT NOT NULL,
    CourseName NVARCHAR(1000),
    CourseShortName NVARCHAR(255),
    CourseCode NVARCHAR(100),
    StartDate DATETIME,
    EndDate DATETIME
);

CREATE TABLE stag_Dim_Quiz (
    SourceQuizID BIGINT NOT NULL,
    CourseKey INT,
    QuizName NVARCHAR(1000),
    TimeOpen DATETIME,
    TimeClose DATETIME,
    TimeLimitMinutes INT,
    MaxGrade DECIMAL(10,2)
);
GO

CREATE TABLE stag_Dim_Assign (
    SourceAssignID BIGINT NOT NULL,
    CourseKey INT,
    AssignName NVARCHAR(1000),
    AllowFromDate DATETIME,
    DueDate DATETIME,
    MaxGrade DECIMAL(10,2)
);
GO

CREATE TABLE stag_Dim_Question (
    SourceQuestionID BIGINT NOT NULL,
    QuestionName NVARCHAR(255),
    QuestionText NVARCHAR(MAX),
    QuestionType NVARCHAR(50),
    DefaultMark DECIMAL(10,2)
);
GO

IF OBJECT_ID('ETL_Watermark', 'U') IS NULL
BEGIN
    CREATE TABLE ETL_Watermark (
        TableName NVARCHAR(100) PRIMARY KEY,
        LastMaxTimestamp BIGINT NOT NULL DEFAULT 0,
        LastRunAt DATETIME NOT NULL DEFAULT GETDATE(),
        LastExtractedRows INT NOT NULL DEFAULT 0,
        LastAffectedRows INT NOT NULL DEFAULT 0
    );
END
GO

IF OBJECT_ID('ETL_Cost_Log', 'U') IS NULL
BEGIN
    CREATE TABLE ETL_Cost_Log (
        LogID BIGINT IDENTITY(1,1) PRIMARY KEY,
        RunBatchID NVARCHAR(50) NOT NULL,
        ETLVersion NVARCHAR(20) NOT NULL,
        TableName NVARCHAR(100) NOT NULL,
        SourceTables NVARCHAR(255) NOT NULL,
        TotalSourceCols INT NOT NULL,
        SelectedCols INT NOT NULL,
        ColReductionPct DECIMAL(5,2) NOT NULL,
        DirectMappedCols INT NOT NULL,
        TransformedCols INT NOT NULL,
        SourceRowCount BIGINT NOT NULL DEFAULT 0,
        ExtractedRowCount BIGINT NOT NULL DEFAULT 0,
        LoadedRowCount BIGINT NOT NULL DEFAULT 0,
        ExtractTimeSec DECIMAL(10,4) NOT NULL DEFAULT 0,
        TransformTimeSec DECIMAL(10,4) NOT NULL DEFAULT 0,
        LoadTimeSec DECIMAL(10,4) NOT NULL DEFAULT 0,
        TotalTimeSec DECIMAL(10,4) NOT NULL DEFAULT 0,
        PeakRamKB DECIMAL(12,2) NOT NULL DEFAULT 0,
        CreatedAt DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO