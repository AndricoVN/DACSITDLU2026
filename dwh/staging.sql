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