-- Leaf procedure: no calls to other procedures
CREATE PROCEDURE [dbo].[sp_GetCustomerDetails]
    @CustomerId INT,
    @IncludeOrders BIT = 0
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        c.CustomerId,
        c.FirstName,
        c.LastName,
        c.Email,
        c.Phone,
        c.CreatedDate
    FROM Customers c
    WHERE c.CustomerId = @CustomerId;

    IF @IncludeOrders = 1
    BEGIN
        SELECT
            o.OrderId,
            o.OrderDate,
            o.TotalAmount,
            o.Status
        FROM Orders o
        WHERE o.CustomerId = @CustomerId
        ORDER BY o.OrderDate DESC;
    END
END
GO
