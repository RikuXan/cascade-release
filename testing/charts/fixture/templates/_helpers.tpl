{{- define "fixture.image" -}}
{{- $image := .Values.image -}}
{{- $tag := $image.tag -}}
{{- $digest := $image.digest -}}
{{- if and (not $tag) (not $digest) -}}
{{- $shipped := $image.shipped | default dict -}}
{{- $tag = $shipped.tag | default .Chart.AppVersion -}}
{{- $digest = $shipped.digest -}}
{{- end -}}
{{ $image.repository }}{{ with $tag }}:{{ . }}{{ end }}{{ with $digest }}@{{ . }}{{ end }}
{{- end }}
