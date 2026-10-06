# Personal Hermes customizations

The fork's main branch follows NousResearch/hermes-agent without personal commits.
The personal/customizations branch records changes needed for this installation.

The live Hermes checkout keeps origin pointed at NousResearch. Its automatic
updater obtains official releases from origin and re-applies the Feishu mobile
indentation patch from the installed script. Do not switch the live checkout to
this archive branch just to update Hermes.

Tracked here:
- The Feishu adapter change converts long leading indentation outside fenced
  code blocks, avoiding missing text in Feishu mobile posts.
- personal/feishu-indent-patch.py is the installed patch applicator.
- personal/hermes-auto-update.sh checks for official updates and reapplies the
  patch after an update.

The installed copies of the scripts live in ~/.hermes/scripts/. If those
scripts change, refresh their copies on this branch. Before bringing official
changes into this branch, check whether the official adapter has fixed the
same issue; retire the patch if it has. Keep runtime configuration, credentials,
logs, and conversation data out of this public fork.

The unrelated local contributor marker is intentionally excluded.
