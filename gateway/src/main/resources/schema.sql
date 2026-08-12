-- Where Spring Security keeps tokens between restarts.
--
-- Spring ships this table's definition, but as a plain CREATE TABLE that fails
-- the second time it runs. Now that the database is a file rather than memory,
-- there is always a second time - hence IF NOT EXISTS.
--
-- The refresh token is the row that matters. Without it a returning user has
-- an access token good for an hour and no way to renew it, and the failure
-- turns up an hour after sign-in rather than at it.
CREATE TABLE IF NOT EXISTS oauth2_authorized_client (
  client_registration_id  varchar(100)  NOT NULL,
  principal_name          varchar(200)  NOT NULL,
  access_token_type       varchar(100)  NOT NULL,
  access_token_value      blob          NOT NULL,
  access_token_issued_at  timestamp     NOT NULL,
  access_token_expires_at timestamp     NOT NULL,
  access_token_scopes     varchar(1000) DEFAULT NULL,
  refresh_token_value     blob          DEFAULT NULL,
  refresh_token_issued_at timestamp     DEFAULT NULL,
  created_at              timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (client_registration_id, principal_name)
);
