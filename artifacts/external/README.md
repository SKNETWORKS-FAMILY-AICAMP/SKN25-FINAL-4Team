# external artifact mount

이 디렉터리는 model/data binary artifact를 외부에서 mount하거나 fetch한 뒤 검증하기 위한 계약 경로입니다.
대형 payload는 repository에 포함하지 않고 `artifacts/manifests/manifest.yaml`, remote checksum, fetch/verify script로 관리합니다.
