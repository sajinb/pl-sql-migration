-- Edge case showcase: linked servers, dynamic SQL, variable calls
CREATE PROCEDURE [dbo].[sp_EdgeCases]
    @Action VARCHAR(50),
    @TableName VARCHAR(100),
    @CustomerId INT
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @sql NVARCHAR(MAX);
    DECLARE @procName VARCHAR(200);

    -- EDGE CASE 1: Linked server (4-part name)
    SELECT *
    FROM [RemoteServer].[RemoteDB].[dbo].[RemoteCustomers]
    WHERE CustomerId = @CustomerId;

    -- EDGE CASE 1b: Linked server EXEC
    EXEC [RemoteServer].[RemoteDB].[dbo].[sp_GetRemoteData] @CustomerId;

    -- EDGE CASE 2a: Dynamic SQL — simple (literal string)
    EXEC('SELECT TOP 10 * FROM Orders ORDER BY OrderDate DESC');

    -- EDGE CASE 2b: Dynamic SQL — templated (sp_executesql)
    SET @sql = N'SELECT * FROM Customers WHERE CustomerId = @id';
    EXEC sp_executesql @sql, N'@id INT', @id = @CustomerId;

    -- EDGE CASE 2c: Dynamic SQL — concatenated
    SET @sql = 'SELECT * FROM ' + @TableName + ' WHERE CustomerId = ' + CAST(@CustomerId AS VARCHAR);
    EXEC(@sql);

    -- EDGE CASE 3a: Variable call — resolvable (single literal)
    SET @procName = 'sp_GetCustomerDetails';
    EXEC @procName @CustomerId;

    -- EDGE CASE 3b: Variable call — conditional
    IF @Action = 'PROCESS'
        SET @procName = 'sp_ProcessOrder';
    ELSE
        SET @procName = 'sp_GetCustomerDetails';
    EXEC @procName @CustomerId;

    -- EDGE CASE 3c: Variable call — dispatch table
    SELECT @procName = ProcedureName
    FROM ActionDispatch
    WHERE ActionType = @Action;
    EXEC @procName @CustomerId;
END
GO
