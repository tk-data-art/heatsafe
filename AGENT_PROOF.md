# Agent Proof of Connection: HeatSafe

## 1. Overview
HeatSafe was built and deployed using a spec-driven agentic workflow combining **AWS Kiro**, **Zed Agent (Claude 3.5 Sonnet)**, and the **AWS CLI**.

## 2. Agent Roles
- **AWS Kiro:** Formulated architectural specifications, user stories, acceptance criteria, and task decomposition (`.kiro/specs/heat-safe/`).
- **Zed Agent (Claude 3.5 Sonnet):** Executed the deterministic NOAA Rothfusz heat index algorithm, FastAPI backend routes, Open-Meteo weather client, and responsive Tailwind UI.
- **AWS CLI & ECS Express Mode:** Automated container registry authentication, image deployment, and serverless hosting on Amazon ECS.

## 3. Verified AWS Identity
- **AWS Account ID:** `130486712171`
- **Region:** `us-east-1` (N. Virginia)
- **Deployment Infrastructure:** Amazon ECR (`130486712171.dkr.ecr.us-east-1.amazonaws.com/heatsafe`) + Amazon ECS Express Mode
- **Live URL:** https://he-a4260a18e5774d7281b3ee39dde3a8b8.ecs.us-east-1.on.aws/
