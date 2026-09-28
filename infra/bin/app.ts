import { App } from "aws-cdk-lib";
import { IntakeStack } from "../lib/intake-stack";

const app = new App();
const ctx = (k: string) => app.node.tryGetContext(k);

new IntakeStack(app, "DentalIntake", {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION ?? "us-east-1" },
  domainName: ctx("domainName"),
  hostedZoneName: ctx("hostedZoneName"),
  alarmEmail: ctx("alarmEmail") || undefined,
  multiAz: ctx("multiAz") !== false && ctx("multiAz") !== "false",
  createCloudTrail: ctx("createCloudTrail") !== false && ctx("createCloudTrail") !== "false",
  cognitoDomainPrefix: ctx("cognitoDomainPrefix"),
});
