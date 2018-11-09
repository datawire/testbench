#!/usr/bin/make -rRf

KUBERNAUT ?= kubernaut
MKOSI ?= sudo testbench-mkosi --cache /var/cache/pacman/pkg/

HOME ?= ~

#

ifeq ($(CMD),)
$(error Must specify a CMD=)
endif

all: $(patsubst %.mkosi,%.tap,$(wildcard environments/*.mkosi))
.PHONY: all

# Set Make's behavior to do the sane thing
.SECONDARY:
.DELETE_ON_ERROR:
.PHONY: FORCE

# Use kubernaut to manage test clusters
%.knaut.claim:
	echo $(subst /,_,$*)-$${USER}-$$(uuidgen) > $@
%.knaut: %.knaut.claim
	$(KUBERNAUT) claims delete "$$(cat $<)"
	$(KUBERNAUT) claims create --name "$$(cat $<)" --cluster-group main >/dev/null
	cp $(HOME)/.kube/"$$(cat $<)".yaml $@
.PHONY: %.knaut.clean
%.knaut.clean:
	[ ! -e $*.knaut.claim ] || $(KUBERNAUT) claims delete "$$(cat $*.knaut.claim)"
	rm -f -- $*.knaut $*.knaut.claim

# Generic mkosi rules
%.mk: %.mkosi FORCE
	@echo $*.osi: $(sort $(shell find $(wildcard $*.mkosi $*.postinst $*.extra))) | ./write-ifchanged $@
-include environments/*.mk
%.osi: %.mkosi %.mk
	$(MKOSI) --force \
	  --output $@ \
	  --default $*.mkosi \
	  $(if $(wildcard $*.postinst),--postinst-script $*.postinst) \
	  $(if $(wildcard $*.extra),--extra-tree $*.extra)

%.tap %.tap.osi: %.osi %.knaut
	cp -T -- $*.osi $@.osi
	$(MKOSI) --output $@.osi --default $*.mkosi withmount install -Dm644 $(abspath $*.knaut) root/.kube/config
	$(MKOSI) --output $@.osi --default $*.mkosi withmount $(abspath install-tap) $(CMD)
	$(MKOSI) --output $@.osi --default $*.mkosi qemu
	$(MKOSI) --output $@.osi --default $*.mkosi withmount cp ./var/log/testbench-run.tap $(abspath $@)
.PRECIOUS: %.tap.osi
