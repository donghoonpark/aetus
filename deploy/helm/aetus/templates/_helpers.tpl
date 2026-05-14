{{/*
Common template helpers for the AETUS chart.
*/}}
{{- define "aetus.name" -}}
{{- default .Chart.Name .Values.global.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "aetus.fullname" -}}
{{- if .Values.global.fullnameOverride -}}
{{- .Values.global.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.global.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "aetus.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | quote }}
app.kubernetes.io/name: {{ include "aetus.name" . | quote }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
{{- end -}}

{{- define "aetus.selectorLabels" -}}
app.kubernetes.io/name: {{ include "aetus.name" . | quote }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{- define "aetus.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "aetus.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "aetus.secretName" -}}
{{- default (printf "%s-secrets" (include "aetus.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "aetus.image" -}}
{{- $root := index . 0 -}}
{{- $image := index . 1 -}}
{{- printf "%s/%s:%s" $root.Values.global.imageRegistry $image.repository $image.tag -}}
{{- end -}}

{{- define "aetus.postgresHost" -}}
{{- if .Values.postgres.enabled -}}
{{- printf "%s-postgres" (include "aetus.fullname" .) -}}
{{- else -}}
{{- .Values.postgres.external.host -}}
{{- end -}}
{{- end -}}

{{- define "aetus.postgresUser" -}}
{{- .Values.postgres.external.username -}}
{{- end -}}

{{- define "aetus.postgresDatabase" -}}
{{- .Values.postgres.external.database -}}
{{- end -}}

{{- define "aetus.postgresJdbcUrl" -}}
{{- printf "jdbc:postgresql://%s:%v/%s" (include "aetus.postgresHost" .) .Values.postgres.external.port (include "aetus.postgresDatabase" .) -}}
{{- end -}}

{{- define "aetus.postgresDsn" -}}
{{- if .Values.secrets.postgresDsn -}}
{{- .Values.secrets.postgresDsn -}}
{{- else -}}
{{- $password := default .Values.secrets.postgresPassword .Values.postgres.external.password -}}
{{- printf "postgresql://%s:%s@%s:%v/%s" (include "aetus.postgresUser" .) $password (include "aetus.postgresHost" .) .Values.postgres.external.port (include "aetus.postgresDatabase" .) -}}
{{- end -}}
{{- end -}}

{{- define "aetus.kafkaBootstrap" -}}
{{- if .Values.kafka.enabled -}}
{{- printf "%s-kafka:9092" (include "aetus.fullname" .) -}}
{{- else -}}
{{- .Values.kafka.external.bootstrapServers -}}
{{- end -}}
{{- end -}}

{{- define "aetus.kafkaConnectUrl" -}}
{{- printf "http://%s-kafka-connect:8083" (include "aetus.fullname" .) -}}
{{- end -}}

{{- define "aetus.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{- printf "redis://%s-redis:6379/0" (include "aetus.fullname" .) -}}
{{- else -}}
{{- .Values.redis.external.url -}}
{{- end -}}
{{- end -}}
