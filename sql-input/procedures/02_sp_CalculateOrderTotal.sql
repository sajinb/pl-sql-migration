-- Leaf procedure with OUTPUT parameters
CREATE PROCEDURE [dbo].[sp_CalculateOrderTotal]
    @OrderId INT,
    @TotalAmount DECIMAL(18,2) OUTPUT,
    @DiscountApplied DECIMAL(18,2) OUTPUT
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @SubTotal DECIMAL(18,2) = 0;
    DECLARE @DiscountPercent DECIMAL(5,2) = 0;

    SELECT @SubTotal = SUM(oi.Quantity * oi.UnitPrice)
    FROM OrderItems oi
    WHERE oi.OrderId = @OrderId;

    SELECT @DiscountPercent = d.DiscountPercent
    FROM Discounts d
    WHERE d.MinOrderAmount <= @SubTotal
    ORDER BY d.MinOrderAmount DESC;

    SET @DiscountApplied = @SubTotal * (@DiscountPercent / 100);
    SET @TotalAmount = @SubTotal - @DiscountApplied;

    UPDATE Orders
    SET TotalAmount = @TotalAmount,
        DiscountAmount = @DiscountApplied
    WHERE OrderId = @OrderId;
END
GO
