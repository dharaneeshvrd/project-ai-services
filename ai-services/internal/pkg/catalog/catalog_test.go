package catalog

import (
	"context"
	"testing"
	"time"
)

func TestListArchitectures(t *testing.T) {
	provider, err := NewCatalogProvider(nil)
	if err != nil {
		t.Fatalf("Failed to create catalog provider: %v", err)
	}

	start := time.Now()
	architectures, err := provider.ListArchitectures()
	elapsed := time.Since(start)

	if err != nil {
		t.Errorf("ListArchitectures() error = %v", err)

		return
	}

	t.Logf("ListArchitectures: Found %d architectures in %v", len(architectures), elapsed)
	for _, arch := range architectures {
		t.Logf("  - %s: %s", arch.ID, arch.Name)
	}
}

func TestLoadArchitecture(t *testing.T) {
	provider, err := NewCatalogProvider(nil)
	if err != nil {
		t.Fatalf("Failed to create catalog provider: %v", err)
	}

	tests := []struct {
		name           string
		architectureID string
		wantErr        bool
	}{
		{
			name:           "Load existing architecture",
			architectureID: "rag",
			wantErr:        false,
		},
		{
			name:           "Load invoice-processing architecture",
			architectureID: "invoice-processing",
			wantErr:        false,
		},
		{
			name:           "Load non-existent architecture",
			architectureID: "non-existent",
			wantErr:        true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			start := time.Now()
			arch, err := provider.LoadArchitecture(tt.architectureID)
			elapsed := time.Since(start)

			if (err != nil) != tt.wantErr {
				t.Errorf("LoadArchitecture() error = %v, wantErr %v", err, tt.wantErr)

				return
			}

			if !tt.wantErr {
				t.Logf("LoadArchitecture(%s): Loaded '%s' in %v", tt.architectureID, arch.Name, elapsed)
			} else {
				t.Logf("LoadArchitecture(%s): Expected error received in %v", tt.architectureID, elapsed)
			}
		})
	}
}
func TestInvoiceProcessingArchitectureDeployOptions(t *testing.T) {
	base, err := NewCatalogProvider(nil)
	if err != nil {
		t.Fatalf("Failed to create catalog provider: %v", err)
	}

	provider, err := base.WithRuntime("podman")
	if err != nil {
		t.Fatalf("Failed to scope provider to podman runtime: %v", err)
	}

	opts, err := provider.GetArchitectureDeployOptions(context.Background(), "invoice-processing")
	if err != nil {
		t.Fatalf("GetArchitectureDeployOptions() error = %v", err)
	}

	// Expected components required by each member service.
	wantComponents := map[string][]string{
		"invoice-processor": {},
		"digitize":          {"vector_store", "embedding", "llm"},
		"extract":           {"llm"},
	}

	if len(opts.Services) != len(wantComponents) {
		t.Fatalf("expected %d services, got %d", len(wantComponents), len(opts.Services))
	}

	for _, svc := range opts.Services {
		want, ok := wantComponents[svc.ID]
		if !ok {
			t.Errorf("unexpected service %q in invoice-processing architecture", svc.ID)

			continue
		}

		got := make(map[string]bool, len(svc.Components))
		for _, c := range svc.Components {
			got[c.Type] = true
		}
		if len(got) != len(want) {
			t.Errorf("service %q: expected components %v, got %v", svc.ID, want, got)
		}
		for _, w := range want {
			if !got[w] {
				t.Errorf("service %q: missing component %q", svc.ID, w)
			}
		}
	}
}

func TestListServices(t *testing.T) {
	provider, err := NewCatalogProvider(nil)
	if err != nil {
		t.Fatalf("Failed to create catalog provider: %v", err)
	}

	start := time.Now()
	services, err := provider.ListServices()
	elapsed := time.Since(start)

	if err != nil {
		t.Errorf("ListServices() error = %v", err)

		return
	}

	t.Logf("ListServices: Found %d services in %v", len(services), elapsed)
	for _, svc := range services {
		t.Logf("  - %s: %s", svc.ID, svc.Name)
	}
}

func TestLoadService(t *testing.T) {
	provider, err := NewCatalogProvider(nil)
	if err != nil {
		t.Fatalf("Failed to create catalog provider: %v", err)
	}

	tests := []struct {
		name      string
		serviceID string
		wantErr   bool
	}{
		{
			name:      "Load regular service",
			serviceID: "chat",
			wantErr:   false,
		},
		{
			name:      "Load non-existent service",
			serviceID: "non-existent",
			wantErr:   true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			start := time.Now()
			service, err := provider.LoadService(tt.serviceID)
			elapsed := time.Since(start)

			if (err != nil) != tt.wantErr {
				t.Errorf("LoadService() error = %v, wantErr %v", err, tt.wantErr)

				return
			}

			if !tt.wantErr {
				t.Logf("LoadService(%s): Loaded '%s' in %v", tt.serviceID, service.Name, elapsed)
			} else {
				t.Logf("LoadService(%s): Expected error received in %v", tt.serviceID, elapsed)
			}
		})
	}
}

func TestListComponents(t *testing.T) {
	provider, err := NewCatalogProvider(nil)
	if err != nil {
		t.Fatalf("Failed to create catalog provider: %v", err)
	}

	start := time.Now()
	components, err := provider.ListComponents()
	elapsed := time.Since(start)

	if err != nil {
		t.Errorf("ListComponents() error = %v", err)

		return
	}

	t.Logf("ListComponents: Found %d components in %v", len(components), elapsed)
	for _, comp := range components {
		t.Logf("  - %s: %s (%s)", comp.ID, comp.Name, comp.ComponentType)
	}
}

func TestLoadComponent(t *testing.T) {
	provider, err := NewCatalogProvider(nil)
	if err != nil {
		t.Fatalf("Failed to create catalog provider: %v", err)
	}

	tests := []struct {
		name          string
		componentType string
		componentID   string
		wantErr       bool
	}{
		{
			name:          "Load opensearch component",
			componentType: "vector_store",
			componentID:   "opensearch",
			wantErr:       false,
		},
		{
			name:          "Load vllm-cpu reranker component",
			componentType: "reranker",
			componentID:   "vllm-cpu",
			wantErr:       false,
		},
		{
			name:          "Load vllm-cpu embedding component",
			componentType: "embedding",
			componentID:   "vllm-cpu",
			wantErr:       false,
		},
		{
			name:          "Load non-existent component",
			componentType: "unknown",
			componentID:   "non-existent-component",
			wantErr:       true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			start := time.Now()
			component, err := provider.LoadComponent(tt.componentType, tt.componentID)
			elapsed := time.Since(start)

			if (err != nil) != tt.wantErr {
				t.Errorf("LoadComponent() error = %v, wantErr %v", err, tt.wantErr)

				return
			}

			if !tt.wantErr {
				t.Logf("LoadComponent(%s/%s): Loaded '%s' (%s) in %v", tt.componentType, tt.componentID, component.Name, component.ComponentType, elapsed)
			} else {
				t.Logf("LoadComponent(%s/%s): Expected error received in %v", tt.componentType, tt.componentID, elapsed)
			}
		})
	}
}

// Made with Bob
