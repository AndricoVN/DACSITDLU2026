CREATE DATABASE [lms_datawarehouse];

USE lms_datawarehouse
GO

CREATE TABLE Dim_Date (
    DateKey INT PRIMARY KEY,
    FullDate DATE NOT NULL,
    DayOfWeek TINYINT NOT NULL,
    DayName NVARCHAR(20) NOT NULL,
    DayOfMonth TINYINT NOT NULL,
    DayOfYear SMALLINT NOT NULL,
    WeekOfYear TINYINT NOT NULL,
    MonthName NVARCHAR(20) NOT NULL,
    MonthOfYear TINYINT NOT NULL,
    Quarter TINYINT NOT NULL,
    Year INT NOT NULL,
    IsWeekend BIT NOT NULL DEFAULT 0
);
CREATE INDEX IX_Dim_Date_FullDate ON Dim_Date(FullDate);
GO

CREATE TABLE Dim_User (
    UserKey INT IDENTITY(1,1) PRIMARY KEY,
    SourceUserID BIGINT NOT NULL UNIQUE,
    Username NVARCHAR(100) NOT NULL,
    PasswordHash NVARCHAR(255) NOT NULL DEFAULT '',
    IdNumber NVARCHAR(100) NULL,
    FullName NVARCHAR(200) NOT NULL,
    Email NVARCHAR(100) NULL,
    Department NVARCHAR(255) NULL,
    Institution NVARCHAR(255) NULL,
    CohortName NVARCHAR(254) NOT NULL DEFAULT N'Chưa phân lớp',
    IsStudent BIT NOT NULL DEFAULT 0,
    CreatedAt DATETIME NULL
);
CREATE INDEX IX_Dim_User_Username ON Dim_User(Username);
GO

CREATE TABLE Dim_Course (
    CourseKey INT IDENTITY(1,1) PRIMARY KEY,
    SourceCourseID BIGINT NOT NULL UNIQUE,
    CourseName NVARCHAR(1000) NOT NULL,
    CourseShortName NVARCHAR(255) NOT NULL,
    CourseCode NVARCHAR(100) NULL,
    StartDate DATETIME NULL,
    EndDate DATETIME NULL
);
GO

CREATE TABLE Dim_Quiz (
    QuizKey INT IDENTITY(1,1) PRIMARY KEY,
    SourceQuizID BIGINT NOT NULL UNIQUE,
    CourseKey INT NOT NULL,
    QuizName NVARCHAR(1000) NOT NULL,
    TimeOpen DATETIME NULL,
    TimeClose DATETIME NULL,
    TimeLimitMinutes INT NOT NULL DEFAULT 0,
    MaxGrade DECIMAL(10,2) NOT NULL DEFAULT 10.00,
    CONSTRAINT FK_DimQuiz_Course FOREIGN KEY (CourseKey) REFERENCES Dim_Course(CourseKey)
);
GO

CREATE TABLE Dim_Assign (
    AssignKey INT IDENTITY(1,1) PRIMARY KEY,
    SourceAssignID BIGINT NOT NULL UNIQUE,
    CourseKey INT NOT NULL,
    AssignName NVARCHAR(1000) NOT NULL,
    AllowFromDate DATETIME NULL,
    DueDate DATETIME NULL,
    MaxGrade DECIMAL(10,2) NOT NULL DEFAULT 10.00,
    CONSTRAINT FK_DimAssign_Course FOREIGN KEY (CourseKey) REFERENCES Dim_Course(CourseKey)
);
GO

CREATE TABLE Dim_Question (
    QuestionKey INT IDENTITY(1,1) PRIMARY KEY,
    SourceQuestionID BIGINT NOT NULL UNIQUE,
    QuestionName NVARCHAR(255) NOT NULL,
    QuestionText NVARCHAR(MAX) NOT NULL,
    QuestionType NVARCHAR(50) NOT NULL,
    DefaultMark DECIMAL(10,2) NOT NULL DEFAULT 1.00
);
GO

CREATE TABLE Fact_Course_Grades (
    CourseGradeKey BIGINT IDENTITY(1,1) PRIMARY KEY,
    UserKey INT NOT NULL,
    CourseKey INT NOT NULL,
    DateKey INT NULL,
    RawGrade DECIMAL(10,2) NULL,
    MaxGrade DECIMAL(10,2) NOT NULL DEFAULT 100.00,
    GradeScaled10 DECIMAL(10,2) NOT NULL,
    LetterGrade VARCHAR(5) NOT NULL DEFAULT 'F',
    GradeClassification NVARCHAR(50) NOT NULL,
    IsPassed BIT NOT NULL DEFAULT 0,
    IsAtRisk BIT NOT NULL DEFAULT 0,
    LastUpdated DATETIME NOT NULL DEFAULT GETDATE(),
    CONSTRAINT FK_FCG_User FOREIGN KEY (UserKey) REFERENCES Dim_User(UserKey),
    CONSTRAINT FK_FCG_Course FOREIGN KEY (CourseKey) REFERENCES Dim_Course(CourseKey),
    CONSTRAINT FK_FCG_Date FOREIGN KEY (DateKey) REFERENCES Dim_Date(DateKey)
);
CREATE INDEX IX_FCG_User ON Fact_Course_Grades(UserKey);
GO

CREATE TABLE Fact_Quiz_Attempts (
    QuizAttemptKey BIGINT IDENTITY(1,1) PRIMARY KEY,
    SourceAttemptID BIGINT NOT NULL,
    UserKey INT NOT NULL,
    QuizKey INT NOT NULL,
    CourseKey INT NOT NULL,
    DateKey INT NULL,
    AttemptNumber INT NOT NULL DEFAULT 1,
    State NVARCHAR(20) NOT NULL,
    DurationSeconds INT NOT NULL DEFAULT 0,
    RawScore DECIMAL(10,2) NULL,
    ScoreScaled10 DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    IsPassed BIT NOT NULL DEFAULT 0,
    TimeStart DATETIME NULL,
    TimeFinish DATETIME NULL,
    CONSTRAINT FK_FQA_User FOREIGN KEY (UserKey) REFERENCES Dim_User(UserKey),
    CONSTRAINT FK_FQA_Quiz FOREIGN KEY (QuizKey) REFERENCES Dim_Quiz(QuizKey),
    CONSTRAINT FK_FQA_Course FOREIGN KEY (CourseKey) REFERENCES Dim_Course(CourseKey),
    CONSTRAINT FK_FQA_Date FOREIGN KEY (DateKey) REFERENCES Dim_Date(DateKey)
);
CREATE INDEX IX_FQA_User ON Fact_Quiz_Attempts(UserKey);
GO

CREATE TABLE Fact_Assign_Submissions (
    SubmissionKey BIGINT IDENTITY(1,1) PRIMARY KEY,
    SourceSubmissionID BIGINT NULL,
    UserKey INT NOT NULL,
    AssignKey INT NOT NULL,
    CourseKey INT NOT NULL,
    DateKey INT NULL,
    SubmissionStatus NVARCHAR(20) NOT NULL,
    IsLate BIT NOT NULL DEFAULT 0,
    DaysLate INT NOT NULL DEFAULT 0,
    Grade DECIMAL(10,2) NULL,
    GradeScaled10 DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    IsPassed BIT NOT NULL DEFAULT 0,
    SubmissionTime DATETIME NULL,
    DueDate DATETIME NULL,
    CONSTRAINT FK_FAS_User FOREIGN KEY (UserKey) REFERENCES Dim_User(UserKey),
    CONSTRAINT FK_FAS_Assign FOREIGN KEY (AssignKey) REFERENCES Dim_Assign(AssignKey),
    CONSTRAINT FK_FAS_Course FOREIGN KEY (CourseKey) REFERENCES Dim_Course(CourseKey),
    CONSTRAINT FK_FAS_Date FOREIGN KEY (DateKey) REFERENCES Dim_Date(DateKey)
);
CREATE INDEX IX_FAS_User ON Fact_Assign_Submissions(UserKey);
GO

CREATE TABLE Fact_Question_Attempts (
    QuestionAttemptKey BIGINT IDENTITY(1,1) PRIMARY KEY,
    SourceQAID BIGINT NOT NULL,
    UserKey INT NOT NULL,
    QuizKey INT NOT NULL,
    QuestionKey INT NOT NULL,
    CourseKey INT NOT NULL,
    DateKey INT NULL,
    Slot INT NOT NULL DEFAULT 1,
    MaxMark DECIMAL(10,2) NOT NULL DEFAULT 1.00,
    EarnedMark DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    Fraction DECIMAL(10,4) NOT NULL DEFAULT 0.0000,
    IsCorrect BIT NOT NULL DEFAULT 0,
    IsWrong BIT NOT NULL DEFAULT 1,
    CONSTRAINT FK_FQAtt_User FOREIGN KEY (UserKey) REFERENCES Dim_User(UserKey),
    CONSTRAINT FK_FQAtt_Quiz FOREIGN KEY (QuizKey) REFERENCES Dim_Quiz(QuizKey),
    CONSTRAINT FK_FQAtt_Question FOREIGN KEY (QuestionKey) REFERENCES Dim_Question(QuestionKey),
    CONSTRAINT FK_FQAtt_Course FOREIGN KEY (CourseKey) REFERENCES Dim_Course(CourseKey)
);
GO