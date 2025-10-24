.PHONY: build export-adhoc export-appstore lint docs

build:
	@./scripts/ios_build.sh

export-adhoc:
	@./scripts/ios_export.sh --method adhoc --export-options apps/ios/PrivateTunnelApp/ExportOptions_adhoc.plist

export-appstore:
	@./scripts/ios_export.sh --method appstore --export-options apps/ios/PrivateTunnelApp/ExportOptions_appstore.plist

lint:
	@echo "Running shell syntax checks"
	@find scripts server -name '*.sh' -print0 | xargs -0 -n1 bash -n
	@echo "Running python bytecode compilation"
	@python3 -m compileall scripts server core

# Convenience target to list documentation entry points
docs:
	@echo "Documentation index:"
	@echo "  - README.md"
	@echo "  - docs/GETTING_STARTED.md"
	@echo "  - docs/BUILD_IOS.md"
	@echo "  - docs/DISTRIBUTION_TESTFLIGHT.md"
	@echo "  - docs/DISTRIBUTION_ADHOC.md"
