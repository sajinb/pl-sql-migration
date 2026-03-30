-- Sample table DDLs for offline schema extraction

CREATE TABLE [dbo].[Customers] (
    [CustomerId] INT IDENTITY(1,1) NOT NULL,
    [FirstName] VARCHAR(100) NOT NULL,
    [LastName] VARCHAR(100) NOT NULL,
    [Email] VARCHAR(200),
    [Phone] VARCHAR(20),
    [CreatedDate] DATETIME DEFAULT GETDATE(),
    CONSTRAINT PK_Customers PRIMARY KEY CLUSTERED ([CustomerId])
);

CREATE TABLE [dbo].[Orders] (
    [OrderId] INT IDENTITY(1,1) NOT NULL,
    [CustomerId] INT NOT NULL,
    [OrderDate] DATETIME NOT NULL,
    [TotalAmount] DECIMAL(18,2),
    [DiscountAmount] DECIMAL(18,2),
    [Status] VARCHAR(50) NOT NULL DEFAULT 'Pending',
    [ProcessedBy] VARCHAR(100),
    [ProcessedDate] DATETIME,
    CONSTRAINT PK_Orders PRIMARY KEY CLUSTERED ([OrderId]),
    CONSTRAINT FK_Orders_Customers FOREIGN KEY ([CustomerId]) REFERENCES [Customers]([CustomerId])
);

CREATE TABLE [dbo].[OrderItems] (
    [OrderItemId] INT IDENTITY(1,1) NOT NULL,
    [OrderId] INT NOT NULL,
    [ProductId] INT NOT NULL,
    [Quantity] INT NOT NULL,
    [UnitPrice] DECIMAL(18,2) NOT NULL,
    CONSTRAINT PK_OrderItems PRIMARY KEY CLUSTERED ([OrderItemId]),
    CONSTRAINT FK_OrderItems_Orders FOREIGN KEY ([OrderId]) REFERENCES [Orders]([OrderId])
);

CREATE TABLE [dbo].[Products] (
    [ProductId] INT IDENTITY(1,1) NOT NULL,
    [ProductName] VARCHAR(200) NOT NULL,
    [Category] VARCHAR(100),
    [Price] DECIMAL(18,2) NOT NULL,
    CONSTRAINT PK_Products PRIMARY KEY CLUSTERED ([ProductId])
);

CREATE TABLE [dbo].[Discounts] (
    [DiscountId] INT IDENTITY(1,1) NOT NULL,
    [MinOrderAmount] DECIMAL(18,2) NOT NULL,
    [DiscountPercent] DECIMAL(5,2) NOT NULL,
    CONSTRAINT PK_Discounts PRIMARY KEY CLUSTERED ([DiscountId])
);

CREATE TABLE [dbo].[OrderAudit] (
    [AuditId] INT IDENTITY(1,1) NOT NULL,
    [OrderId] INT NOT NULL,
    [Action] VARCHAR(50) NOT NULL,
    [ActionBy] VARCHAR(100),
    [ActionDate] DATETIME DEFAULT GETDATE(),
    [Details] VARCHAR(500),
    CONSTRAINT PK_OrderAudit PRIMARY KEY CLUSTERED ([AuditId])
);

CREATE TABLE [dbo].[ErrorLog] (
    [ErrorId] INT IDENTITY(1,1) NOT NULL,
    [ProcedureName] VARCHAR(200),
    [ErrorMessage] VARCHAR(MAX),
    [ErrorDate] DATETIME DEFAULT GETDATE(),
    CONSTRAINT PK_ErrorLog PRIMARY KEY CLUSTERED ([ErrorId])
);

CREATE TABLE [dbo].[MonthlyReports] (
    [ReportId] INT IDENTITY(1,1) NOT NULL,
    [ReportMonth] INT NOT NULL,
    [ReportYear] INT NOT NULL,
    [TotalOrders] INT,
    [TotalRevenue] DECIMAL(18,2),
    [TotalDiscounts] DECIMAL(18,2),
    [GeneratedBy] VARCHAR(100),
    [GeneratedDate] DATETIME DEFAULT GETDATE(),
    CONSTRAINT PK_MonthlyReports PRIMARY KEY CLUSTERED ([ReportId])
);

CREATE TABLE [dbo].[MonthlyReportDetails] (
    [DetailId] INT IDENTITY(1,1) NOT NULL,
    [ReportId] INT NOT NULL,
    [Category] VARCHAR(100),
    [OrderCount] INT,
    [TotalRevenue] DECIMAL(18,2),
    [AvgOrderValue] DECIMAL(18,2),
    CONSTRAINT PK_MonthlyReportDetails PRIMARY KEY CLUSTERED ([DetailId]),
    CONSTRAINT FK_ReportDetails_Reports FOREIGN KEY ([ReportId]) REFERENCES [MonthlyReports]([ReportId])
);

CREATE TABLE [dbo].[MonthlyReportTopCustomers] (
    [Id] INT IDENTITY(1,1) NOT NULL,
    [ReportId] INT NOT NULL,
    [CustomerId] INT,
    [CustomerName] VARCHAR(200),
    [OrderCount] INT,
    [TotalSpent] DECIMAL(18,2),
    [Ranking] INT,
    CONSTRAINT PK_MonthlyReportTopCustomers PRIMARY KEY CLUSTERED ([Id]),
    CONSTRAINT FK_TopCustomers_Reports FOREIGN KEY ([ReportId]) REFERENCES [MonthlyReports]([ReportId])
);

CREATE TABLE [dbo].[ActionDispatch] (
    [ActionType] VARCHAR(50) NOT NULL,
    [ProcedureName] VARCHAR(200) NOT NULL,
    CONSTRAINT PK_ActionDispatch PRIMARY KEY CLUSTERED ([ActionType])
);
