-- Mid-level: calls sp_CalculateOrderTotal and sp_GetCustomerDetails
-- Uses temp table and TRY/CATCH
CREATE PROCEDURE [dbo].[sp_ProcessOrder]
    @OrderId INT,
    @ProcessedBy VARCHAR(100)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @CustomerId INT;
    DECLARE @TotalAmount DECIMAL(18,2);
    DECLARE @DiscountApplied DECIMAL(18,2);
    DECLARE @ErrorMessage VARCHAR(500);

    CREATE TABLE #ProcessingLog (
        StepNumber INT,
        StepName VARCHAR(100),
        StepStatus VARCHAR(20),
        StepTimestamp DATETIME DEFAULT GETDATE(),
        Details VARCHAR(500)
    );

    BEGIN TRY
        BEGIN TRANSACTION;

        INSERT INTO #ProcessingLog (StepNumber, StepName, StepStatus)
        VALUES (1, 'Validate Order', 'Started');

        SELECT @CustomerId = CustomerId
        FROM Orders
        WHERE OrderId = @OrderId AND Status = 'Pending';

        IF @CustomerId IS NULL
        BEGIN
            RAISERROR('Order %d not found or not in Pending status', 16, 1, @OrderId);
        END

        UPDATE #ProcessingLog SET StepStatus = 'Completed' WHERE StepNumber = 1;

        INSERT INTO #ProcessingLog (StepNumber, StepName, StepStatus)
        VALUES (2, 'Calculate Total', 'Started');

        EXEC sp_CalculateOrderTotal @OrderId, @TotalAmount OUTPUT, @DiscountApplied OUTPUT;

        UPDATE #ProcessingLog SET StepStatus = 'Completed',
            Details = 'Total: ' + CAST(@TotalAmount AS VARCHAR) WHERE StepNumber = 2;

        INSERT INTO #ProcessingLog (StepNumber, StepName, StepStatus)
        VALUES (3, 'Verify Customer', 'Started');

        EXEC sp_GetCustomerDetails @CustomerId;

        UPDATE #ProcessingLog SET StepStatus = 'Completed' WHERE StepNumber = 3;

        UPDATE Orders
        SET Status = 'Processing',
            ProcessedBy = @ProcessedBy,
            ProcessedDate = GETDATE()
        WHERE OrderId = @OrderId;

        INSERT INTO OrderAudit (OrderId, Action, ActionBy, ActionDate, Details)
        VALUES (@OrderId, 'PROCESS', @ProcessedBy, GETDATE(),
                'Total: ' + CAST(@TotalAmount AS VARCHAR) + ', Discount: ' + CAST(@DiscountApplied AS VARCHAR));

        COMMIT TRANSACTION;

        SELECT * FROM #ProcessingLog ORDER BY StepNumber;

    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;

        SET @ErrorMessage = ERROR_MESSAGE();

        INSERT INTO #ProcessingLog (StepNumber, StepName, StepStatus, Details)
        VALUES (99, 'ERROR', 'Failed', @ErrorMessage);

        INSERT INTO ErrorLog (ProcedureName, ErrorMessage, ErrorDate)
        VALUES ('sp_ProcessOrder', @ErrorMessage, GETDATE());

        SELECT * FROM #ProcessingLog ORDER BY StepNumber;

        RAISERROR(@ErrorMessage, 16, 1);
    END CATCH
END
GO
