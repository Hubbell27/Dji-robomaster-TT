import * as path from "path";
import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
  aws_certificatemanager as acm,
  aws_cloudtrail as cloudtrail,
  aws_cloudwatch as cw,
  aws_cloudwatch_actions as cwActions,
  aws_cognito as cognito,
  aws_ec2 as ec2,
  aws_ecs as ecs,
  aws_ecs_patterns as ecsPatterns,
  aws_elasticloadbalancingv2 as elbv2,
  aws_iam as iam,
  aws_kms as kms,
  aws_logs as logs,
  aws_rds as rds,
  aws_route53 as route53,
  aws_s3 as s3,
  aws_secretsmanager as secretsmanager,
  aws_sns as sns,
  aws_sns_subscriptions as subs,
  aws_wafv2 as wafv2,
} from "aws-cdk-lib";
import { Construct } from "constructs";

export interface IntakeStackProps extends StackProps {
  /** Public hostname for the app, e.g. intake.example-dental.com */
  domainName: string;
  /** Route 53 public hosted zone that contains domainName. */
  hostedZoneName: string;
  /** Where operational/security alarms are emailed. */
  alarmEmail?: string;
  multiAz: boolean;
  /** Set false if an organization-wide CloudTrail already covers this account. */
  createCloudTrail: boolean;
  /** Globally unique prefix for the Cognito hosted sign-in domain. */
  cognitoDomainPrefix: string;
}

/**
 * HIPAA-oriented deployment:
 *  - One customer-managed KMS key (rotation on) encrypts RDS, S3, logs,
 *    secrets, SNS, and the app's per-record envelope keys.
 *  - Database in isolated subnets, TLS enforced (rds.force_ssl=1).
 *  - App on Fargate in private subnets, reachable only via an HTTPS ALB with
 *    TLS 1.2+/1.3 policy and AWS WAF.
 *  - Cognito user pool with mandatory TOTP MFA and no self sign-up.
 *  - Audit trail: app audit events → encrypted CloudWatch Logs (6-year
 *    retention) + append-only DB table; CloudTrail incl. S3 data events.
 *
 * Only use HIPAA-eligible services under a signed AWS BAA; everything here is.
 */
export class IntakeStack extends Stack {
  constructor(scope: Construct, id: string, props: IntakeStackProps) {
    super(scope, id, props);

    // ------------------------------------------------------------- encryption
    const key = new kms.Key(this, "PhiKey", {
      alias: "alias/dental-intake-phi",
      description: "Encrypts all dental intake PHI at rest",
      enableKeyRotation: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    key.grantEncryptDecrypt(new iam.ServicePrincipal(`logs.${this.region}.amazonaws.com`));

    // --------------------------------------------------------------- network
    const vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        { name: "public", subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: "app", subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 },
        { name: "data", subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
      gatewayEndpoints: { S3: { service: ec2.GatewayVpcEndpointAwsService.S3 } },
    });
    vpc.addInterfaceEndpoint("KmsEndpoint", { service: ec2.InterfaceVpcEndpointAwsService.KMS });
    vpc.addInterfaceEndpoint("SecretsEndpoint", { service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER });
    const flowLogGroup = new logs.LogGroup(this, "FlowLogs", {
      retention: logs.RetentionDays.ONE_YEAR,
      encryptionKey: key,
    });
    vpc.addFlowLog("FlowLog", { destination: ec2.FlowLogDestination.toCloudWatchLogs(flowLogGroup) });

    // ------------------------------------------------------------ log bucket
    // ALB access logs require SSE-S3 (not KMS). They contain no PHI: patient
    // link tokens travel in the URL fragment, which is never sent to the server.
    const logBucket = new s3.Bucket(this, "AccessLogs", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
      lifecycleRules: [{
        transitions: [{ storageClass: s3.StorageClass.GLACIER, transitionAfter: Duration.days(90) }],
        expiration: Duration.days(2190),
      }],
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // ------------------------------------------------------------ PHI bucket
    const phiBucket = new s3.Bucket(this, "PhiFiles", {
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: key,
      bucketKeyEnabled: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      minimumTLSVersion: 1.2,
      versioned: true,
      serverAccessLogsBucket: logBucket,
      serverAccessLogsPrefix: "s3-phi/",
      lifecycleRules: [{ noncurrentVersionExpiration: Duration.days(90) }],
      removalPolicy: RemovalPolicy.RETAIN,
    });
    phiBucket.addToResourcePolicy(new iam.PolicyStatement({
      sid: "DenyUnencryptedUploads",
      effect: iam.Effect.DENY,
      principals: [new iam.AnyPrincipal()],
      actions: ["s3:PutObject"],
      resources: [phiBucket.arnForObjects("*")],
      conditions: { StringNotEquals: { "s3:x-amz-server-side-encryption": "aws:kms" } },
    }));

    // --------------------------------------------------------------- database
    const dbSg = new ec2.SecurityGroup(this, "DbSg", { vpc, allowAllOutbound: false, description: "Intake Postgres" });
    const params = new rds.ParameterGroup(this, "DbParams", {
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.VER_16 }),
      parameters: {
        "rds.force_ssl": "1",
        log_connections: "1",
        log_disconnections: "1",
        // Never log statement text/parameters: they can contain ciphertext ids or PHI.
        log_statement: "ddl",
      },
    });
    const db = new rds.DatabaseInstance(this, "Db", {
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.VER_16 }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MEDIUM),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [dbSg],
      databaseName: "intake",
      credentials: rds.Credentials.fromGeneratedSecret("intake_admin", { encryptionKey: key }),
      storageEncrypted: true,
      storageEncryptionKey: key,
      multiAz: props.multiAz,
      allocatedStorage: 20,
      maxAllocatedStorage: 100,
      backupRetention: Duration.days(35),
      deletionProtection: true,
      removalPolicy: RemovalPolicy.SNAPSHOT,
      parameterGroup: params,
      enablePerformanceInsights: true,
      performanceInsightEncryptionKey: key,
      cloudwatchLogsExports: ["postgresql"],
      cloudwatchLogsRetention: logs.RetentionDays.ONE_YEAR,
      autoMinorVersionUpgrade: true,
      copyTagsToSnapshot: true,
    });

    // ----------------------------------------------------------------- Cognito
    const appUrl = `https://${props.domainName}`;
    const userPool = new cognito.UserPool(this, "StaffPool", {
      userPoolName: "dental-intake-staff",
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      signInCaseSensitive: false,
      standardAttributes: { email: { required: true, mutable: true }, fullname: { required: false, mutable: true } },
      mfa: cognito.Mfa.REQUIRED,
      mfaSecondFactor: { otp: true, sms: false },
      passwordPolicy: {
        minLength: 12,
        requireDigits: true,
        requireLowercase: true,
        requireUppercase: true,
        requireSymbols: true,
        tempPasswordValidity: Duration.days(3),
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      userInvitation: {
        emailSubject: "Your patient intake staff account",
        emailBody: `You've been invited to the patient intake dashboard at ${appUrl}/staff. Username: {username} Temporary password: {####} You will be asked to set a new password and an authenticator app.`,
      },
      deletionProtection: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const domain = userPool.addDomain("HostedDomain", {
      cognitoDomain: { domainPrefix: props.cognitoDomainPrefix },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });
    const client = userPool.addClient("Spa", {
      generateSecret: false,
      authFlows: { user: false, userPassword: false, userSrp: false, adminUserPassword: false, custom: false },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: [`${appUrl}/staff/callback`],
        logoutUrls: [`${appUrl}/staff`],
      },
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
      idTokenValidity: Duration.minutes(60),
      accessTokenValidity: Duration.minutes(60),
      refreshTokenValidity: Duration.hours(12),
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
    });
    new cognito.CfnManagedLoginBranding(this, "LoginBranding", {
      userPoolId: userPool.userPoolId,
      clientId: client.userPoolClientId,
      useCognitoProvidedValues: true,
    });

    // ----------------------------------------------------------------- secrets
    const patientSessionSecret = new secretsmanager.Secret(this, "PatientSessionSecret", {
      description: "HMAC key for short-lived patient session tokens",
      encryptionKey: key,
      generateSecretString: { passwordLength: 64, excludePunctuation: true },
    });

    const indexKeySecret = new secretsmanager.Secret(this, "PatientIndexKey", {
      description: "HMAC key for the returning-patient lookup index",
      encryptionKey: key,
      generateSecretString: { passwordLength: 64, excludePunctuation: true },
    });

    // -------------------------------------------------------------- app (ECS)
    const zone = route53.HostedZone.fromLookup(this, "Zone", { domainName: props.hostedZoneName });
    const certificate = new acm.Certificate(this, "Cert", {
      domainName: props.domainName,
      validation: acm.CertificateValidation.fromDns(zone),
    });

    const appLogs = new logs.LogGroup(this, "AppLogs", {
      logGroupName: "/dental-intake/app",
      retention: logs.RetentionDays.SIX_YEARS, // HIPAA documentation retention
      encryptionKey: key,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const cluster = new ecs.Cluster(this, "Cluster", {
      vpc,
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
    });

    const service = new ecsPatterns.ApplicationLoadBalancedFargateService(this, "App", {
      cluster,
      cpu: 512,
      memoryLimitMiB: 1024,
      desiredCount: 2,
      taskSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      publicLoadBalancer: true,
      protocol: elbv2.ApplicationProtocol.HTTPS,
      certificate,
      sslPolicy: elbv2.SslPolicy.RECOMMENDED_TLS,
      redirectHTTP: true,
      domainName: props.domainName,
      domainZone: zone,
      circuitBreaker: { rollback: true },
      minHealthyPercent: 100,
      taskImageOptions: {
        image: ecs.ContainerImage.fromAsset(path.join(__dirname, "..", ".."), { exclude: ["infra"] }),
        containerPort: 8000,
        logDriver: ecs.LogDrivers.awsLogs({ streamPrefix: "app", logGroup: appLogs }),
        environment: {
          INTAKE_ENVIRONMENT: "production",
          INTAKE_DEPLOYMENT: "aws",
          INTAKE_PUBLIC_BASE_URL: appUrl,
          INTAKE_AUTH_MODE: "cognito",
          INTAKE_AWS_REGION: this.region,
          AWS_REGION: this.region,
          INTAKE_COGNITO_USER_POOL_ID: userPool.userPoolId,
          INTAKE_COGNITO_CLIENT_ID: client.userPoolClientId,
          INTAKE_COGNITO_DOMAIN: domain.baseUrl(),
          INTAKE_KEY_PROVIDER: "kms",
          INTAKE_KMS_KEY_ID: key.keyArn,
          INTAKE_STORAGE_BACKEND: "s3",
          INTAKE_S3_BUCKET: phiBucket.bucketName,
          INTAKE_DATABASE_SSLMODE: "verify-full",
          INTAKE_DB_NAME: "intake",
        },
        secrets: {
          INTAKE_DB_HOST: ecs.Secret.fromSecretsManager(db.secret!, "host"),
          INTAKE_DB_USERNAME: ecs.Secret.fromSecretsManager(db.secret!, "username"),
          INTAKE_DB_PASSWORD: ecs.Secret.fromSecretsManager(db.secret!, "password"),
          INTAKE_PATIENT_SESSION_SECRET: ecs.Secret.fromSecretsManager(patientSessionSecret),
          INTAKE_INDEX_KEY: ecs.Secret.fromSecretsManager(indexKeySecret),
        },
      },
    });
    const container = service.taskDefinition.defaultContainer!;
    container.addUlimits({ name: ecs.UlimitName.NOFILE, softLimit: 65536, hardLimit: 65536 });
    // Read-only root filesystem; uploads larger than 1 MB spool to /tmp, so give
    // the task a writable ephemeral scratch volume there.
    service.taskDefinition.addVolume({ name: "tmp" });
    container.addMountPoints({ sourceVolume: "tmp", containerPath: "/tmp", readOnly: false });
    const cfnTask = service.taskDefinition.node.defaultChild as ecs.CfnTaskDefinition;
    cfnTask.addPropertyOverride("ContainerDefinitions.0.ReadonlyRootFilesystem", true);

    service.targetGroup.configureHealthCheck({ path: "/api/health", healthyHttpCodes: "200", interval: Duration.seconds(30) });
    service.targetGroup.setAttribute("deregistration_delay.timeout_seconds", "30");
    service.loadBalancer.setAttribute("routing.http.drop_invalid_header_fields.enabled", "true");
    service.loadBalancer.setAttribute("deletion_protection.enabled", "true");
    service.loadBalancer.logAccessLogs(logBucket, "alb");
    db.connections.allowDefaultPortFrom(service.service, "App to Postgres");

    const scaling = service.service.autoScaleTaskCount({ minCapacity: 2, maxCapacity: 6 });
    scaling.scaleOnCpuUtilization("Cpu", { targetUtilizationPercent: 60 });

    // Least-privilege task role.
    const taskRole = service.taskDefinition.taskRole;
    key.grant(taskRole, "kms:GenerateDataKey", "kms:Decrypt", "kms:Encrypt");
    phiBucket.grantReadWrite(taskRole);
    phiBucket.grantDelete(taskRole);
    taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: [
        "cognito-idp:AdminCreateUser",
        "cognito-idp:AdminDisableUser",
        "cognito-idp:AdminEnableUser",
        "cognito-idp:AdminUserGlobalSignOut",
      ],
      resources: [userPool.userPoolArn],
    }));

    // --------------------------------------------------------------------- WAF
    const managed = (name: string, priority: number, excluded: string[] = []): wafv2.CfnWebACL.RuleProperty => ({
      name,
      priority,
      overrideAction: { none: {} },
      statement: {
        managedRuleGroupStatement: {
          vendorName: "AWS",
          name,
          ruleActionOverrides: excluded.map((r) => ({ name: r, actionToUse: { count: {} } })),
        },
      },
      visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: name, sampledRequestsEnabled: false },
    });
    const waf = new wafv2.CfnWebACL(this, "Waf", {
      scope: "REGIONAL",
      defaultAction: { allow: {} },
      visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: "intake-waf", sampledRequestsEnabled: false },
      rules: [
        // Card photo uploads exceed the common rule set's 8 KB body limit.
        managed("AWSManagedRulesCommonRuleSet", 1, ["SizeRestrictions_BODY", "CrossSiteScripting_BODY"]),
        managed("AWSManagedRulesKnownBadInputsRuleSet", 2),
        managed("AWSManagedRulesAmazonIpReputationList", 3),
        {
          name: "RateLimitDobVerify",
          priority: 10,
          action: { block: {} },
          statement: {
            rateBasedStatement: {
              limit: 30,
              evaluationWindowSec: 300,
              aggregateKeyType: "IP",
              scopeDownStatement: {
                byteMatchStatement: {
                  searchString: "/api/patient/verify",
                  fieldToMatch: { uriPath: {} },
                  positionalConstraint: "EXACTLY",
                  textTransformations: [{ priority: 0, type: "NONE" }],
                },
              },
            },
          },
          visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: "rate-verify", sampledRequestsEnabled: false },
        },
        {
          name: "RateLimitAll",
          priority: 11,
          action: { block: {} },
          statement: { rateBasedStatement: { limit: 2000, evaluationWindowSec: 300, aggregateKeyType: "IP" } },
          visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: "rate-all", sampledRequestsEnabled: false },
        },
      ],
    });
    new wafv2.CfnWebACLAssociation(this, "WafAssoc", {
      resourceArn: service.loadBalancer.loadBalancerArn,
      webAclArn: waf.attrArn,
    });

    // -------------------------------------------------------------- CloudTrail
    if (props.createCloudTrail) {
      const trail = new cloudtrail.Trail(this, "Trail", {
        encryptionKey: key,
        enableFileValidation: true,
        includeGlobalServiceEvents: true,
        isMultiRegionTrail: true,
        sendToCloudWatchLogs: true,
        cloudWatchLogsRetention: logs.RetentionDays.SIX_YEARS,
      });
      trail.addS3EventSelector([{ bucket: phiBucket }], { readWriteType: cloudtrail.ReadWriteType.ALL });
      key.grantEncryptDecrypt(new iam.ServicePrincipal("cloudtrail.amazonaws.com"));
    }

    // ------------------------------------------------------------------ alarms
    const topic = new sns.Topic(this, "Alarms", { masterKey: key });
    key.grantEncryptDecrypt(new iam.ServicePrincipal("cloudwatch.amazonaws.com"));
    if (props.alarmEmail) topic.addSubscription(new subs.EmailSubscription(props.alarmEmail));
    const notify = new cwActions.SnsAction(topic);

    const deniedFilter = appLogs.addMetricFilter("AccessDenied", {
      filterPattern: logs.FilterPattern.stringValue("$.outcome", "=", "denied"),
      metricNamespace: "DentalIntake",
      metricName: "AccessDenied",
      metricValue: "1",
    });
    new cw.Alarm(this, "AccessDeniedAlarm", {
      metric: deniedFilter.metric({ statistic: "Sum", period: Duration.minutes(5) }),
      threshold: 10,
      evaluationPeriods: 1,
      alarmDescription: "Repeated denied access attempts to patient data or admin functions",
      treatMissingData: cw.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(notify);

    const lockoutFilter = appLogs.addMetricFilter("DobFailures", {
      filterPattern: logs.FilterPattern.all(
        logs.FilterPattern.stringValue("$.action", "=", "patient.verify"),
        logs.FilterPattern.stringValue("$.outcome", "=", "failure"),
      ),
      metricNamespace: "DentalIntake",
      metricName: "PatientVerifyFailures",
      metricValue: "1",
    });
    new cw.Alarm(this, "VerifyFailuresAlarm", {
      metric: lockoutFilter.metric({ statistic: "Sum", period: Duration.minutes(15) }),
      threshold: 25,
      evaluationPeriods: 1,
      alarmDescription: "Unusual number of failed patient link / DOB verifications (possible link guessing)",
      treatMissingData: cw.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(notify);

    new cw.Alarm(this, "Alb5xx", {
      metric: service.loadBalancer.metrics.httpCodeTarget(elbv2.HttpCodeTarget.TARGET_5XX_COUNT, { period: Duration.minutes(5) }),
      threshold: 10,
      evaluationPeriods: 1,
      treatMissingData: cw.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(notify);

    new cw.Alarm(this, "UnhealthyHosts", {
      metric: service.targetGroup.metrics.unhealthyHostCount({ period: Duration.minutes(1) }),
      threshold: 1,
      evaluationPeriods: 3,
      treatMissingData: cw.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(notify);

    // ----------------------------------------------------------------- outputs
    new CfnOutput(this, "AppUrl", { value: appUrl });
    new CfnOutput(this, "UserPoolId", { value: userPool.userPoolId });
    new CfnOutput(this, "ClusterName", { value: cluster.clusterName });
    new CfnOutput(this, "TaskDefinitionArn", { value: service.taskDefinition.taskDefinitionArn });
    new CfnOutput(this, "PrivateSubnets", {
      value: vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }).subnetIds.join(","),
    });
    new CfnOutput(this, "AppSecurityGroup", { value: service.service.connections.securityGroups[0].securityGroupId });
  }
}
