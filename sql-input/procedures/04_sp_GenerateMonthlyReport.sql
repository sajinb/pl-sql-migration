-- Top-level: calls sp_ProcessOrder, uses cursor, 3 temp tables
-- Large procedure — will trigger chunking
CREATE PROCEDURE [dbo].[sp_GenerateMonthlyReport]
    @ReportMonth INT,
    @ReportYear INT,
    @GeneratedBy VARCHAR(100)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @StartDate DATETIME;
    DECLARE @EndDate DATETIME;
    DECLARE @OrderId INT;
    DECLARE @TotalOrders INT = 0;
    DECLARE @TotalRevenue DECIMAL(18,2) = 0;
    DECLARE @TotalDiscounts DECIMAL(18,2) = 0;
    DECLARE @ReportId INT;

    SET @StartDate = DATEFROMPARTS(@ReportYear, @ReportMonth, 1);
    SET @EndDate = EOMONTH(@StartDate);

    CREATE TABLE #MonthlyOrders (
        OrderId INT,
        CustomerId INT,
        CustomerName VARCHAR(200),
        OrderDate DATETIME,
        TotalAmount DECIMAL(18,2),
        DiscountAmount DECIMAL(18,2),
        Status VARCHAR(50),
        Category VARCHAR(100)
    );

    CREATE TABLE #CategorySummary (
        Category VARCHAR(100),
        OrderCount INT,
        TotalRevenue DECIMAL(18,2),
        AvgOrderValue DECIMAL(18,2)
    );

    CREATE TABLE #CustomerRankings (
        CustomerId INT,
        CustomerName VARCHAR(200),
        OrderCount INT,
        TotalSpent DECIMAL(18,2),
        Ranking INT
    );

    BEGIN TRY
        BEGIN TRANSACTION;

        INSERT INTO #MonthlyOrders (OrderId, CustomerId, CustomerName, OrderDate,
                                     TotalAmount, DiscountAmount, Status, Category)
        SELECT
            o.OrderId,
            o.CustomerId,
            c.FirstName + ' ' + c.LastName,
            o.OrderDate,
            o.TotalAmount,
            ISNULL(o.DiscountAmount, 0),
            o.Status,
            p.Category
        FROM Orders o
        INNER JOIN Customers c ON o.CustomerId = c.CustomerId
        INNER JOIN OrderItems oi ON o.OrderId = oi.OrderId
        INNER JOIN Products p ON oi.ProductId = p.ProductId
        WHERE o.OrderDate BETWEEN @StartDate AND @EndDate;

        DECLARE order_cursor CURSOR FOR
            SELECT OrderId FROM #MonthlyOrders WHERE Status = 'Pending';

        OPEN order_cursor;
        FETCH NEXT FROM order_cursor INTO @OrderId;

        WHILE @@FETCH_STATUS = 0
        BEGIN
            EXEC sp_ProcessOrder @OrderId, @GeneratedBy;
            SET @TotalOrders = @TotalOrders + 1;
            FETCH NEXT FROM order_cursor INTO @OrderId;
        END

        CLOSE order_cursor;
        DEALLOCATE order_cursor;

        INSERT INTO #CategorySummary (Category, OrderCount, TotalRevenue, AvgOrderValue)
        SELECT
            Category,
            COUNT(DISTINCT OrderId),
            SUM(TotalAmount),
            AVG(TotalAmount)
        FROM #MonthlyOrders
        WHERE Status IN ('Processing', 'Completed', 'Shipped')
        GROUP BY Category;

        INSERT INTO #CustomerRankings (CustomerId, CustomerName, OrderCount, TotalSpent, Ranking)
        SELECT
            CustomerId,
            CustomerName,
            COUNT(DISTINCT OrderId),
            SUM(TotalAmount),
            ROW_NUMBER() OVER (ORDER BY SUM(TotalAmount) DESC)
        FROM #MonthlyOrders
        WHERE Status IN ('Processing', 'Completed', 'Shipped')
        GROUP BY CustomerId, CustomerName;

        SELECT
            @TotalOrders = COUNT(DISTINCT OrderId),
            @TotalRevenue = ISNULL(SUM(TotalAmount), 0),
            @TotalDiscounts = ISNULL(SUM(DiscountAmount), 0)
        FROM #MonthlyOrders
        WHERE Status IN ('Processing', 'Completed', 'Shipped');

        INSERT INTO MonthlyReports (ReportMonth, ReportYear, TotalOrders, TotalRevenue,
                                     TotalDiscounts, GeneratedBy, GeneratedDate)
        VALUES (@ReportMonth, @ReportYear, @TotalOrders, @TotalRevenue,
                @TotalDiscounts, @GeneratedBy, GETDATE());

        SET @ReportId = SCOPE_IDENTITY();

        INSERT INTO MonthlyReportDetails (ReportId, Category, OrderCount, TotalRevenue, AvgOrderValue)
        SELECT @ReportId, Category, OrderCount, TotalRevenue, AvgOrderValue
        FROM #CategorySummary;

        INSERT INTO MonthlyReportTopCustomers (ReportId, CustomerId, CustomerName,
                                                OrderCount, TotalSpent, Ranking)
        SELECT @ReportId, CustomerId, CustomerName, OrderCount, TotalSpent, Ranking
        FROM #CustomerRankings
        WHERE Ranking <= 10;

        COMMIT TRANSACTION;

        SELECT 'Report generated successfully' AS Message,
               @ReportId AS ReportId,
               @TotalOrders AS TotalOrders,
               @TotalRevenue AS TotalRevenue,
               @TotalDiscounts AS TotalDiscounts;

        SELECT * FROM #CategorySummary ORDER BY TotalRevenue DESC;
        SELECT * FROM #CustomerRankings WHERE Ranking <= 10 ORDER BY Ranking;

    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;

        INSERT INTO ErrorLog (ProcedureName, ErrorMessage, ErrorDate)
        VALUES ('sp_GenerateMonthlyReport', ERROR_MESSAGE(), GETDATE());

        RAISERROR('Monthly report generation failed: %s', 16, 1, ERROR_MESSAGE());
    END CATCH
END
GO
