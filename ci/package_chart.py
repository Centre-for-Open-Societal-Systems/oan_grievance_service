"""Package helm/oan-grievance-service with this build's image, and publish that archive to oan-helm.

Run by the Jenkins stages "Package chart" and "Publish chart" of every develop build:

    package_chart.py version   prints the chart version, which is also the image tag of the build:
                               0.0.1-develop.<BUILD_NUMBER>, e.g. 0.0.1-develop.57 (develop or staging)
    package_chart.py package   writes built-chart/oan-grievance-service-<version>.tgz
    package_chart.py promote   staging only: the develop chart of this source tree with its image also tagged <version>-staging.N
    package_chart.py publish   adds that archive to repository/develop/ of oan-helm
    package_chart.py gateway-package   writes built-gateway-chart/oan-grievance-gateway-<version>.tgz, the routes
                               chart of apisix/k8s, with the version of the build
    package_chart.py gateway-publish   adds that archive to repository/develop/ of oan-helm

The Chart.yaml annotations and the publisher come from scripts/ci of oan-helm, so this chart is listed like the registry
charts. The version is 0.0.1-develop.N, which publish_chart.py sends to repository/develop/. The chart keeps its frappe-service dependency vendored in helm/oan-grievance-service/charts,
so, unlike the registry charts, nothing is downloaded.

version needs BRANCH_NAME (develop) and BUILD_NUMBER. package needs IMAGE_REPOSITORY (registry/repository, no tag), IMAGE_TAG, SOURCE_COMMIT, BRANCH_NAME and COMMIT_SUBJECT.
publish needs GH_TOKEN with push access to oan-helm. DRY_RUN=1 publishes nothing. Standard library only.

Exit codes: 0 done (or already published by an earlier run of this commit), 1 failure, 3 no credential.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHART = "oan-grievance-service"
GATEWAY_CHART = "oan-grievance-gateway"
OAN_HELM_SLUG = "Centre-for-Open-Societal-Systems/oan-helm"
OAN_HELM = "https://github.com/%s.git" % OAN_HELM_SLUG
ALREADY_PUBLISHED = 4
OUTPUT = ROOT / "built-chart"
GATEWAY_OUTPUT = ROOT / "built-gateway-chart"


def run(command, cwd=None, env=None):
	return subprocess.run(
		command, cwd=str(cwd or ROOT), env=env, check=True, stdout=subprocess.PIPE, text=True
	).stdout


def fetch_publisher(work):
	"""scripts/ci of oan-helm main, without its repository/ archives."""
	target = work / "oan-helm"
	run(["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse", OAN_HELM, str(target)])
	run(["git", "sparse-checkout", "set", "scripts/ci"], cwd=target)
	return target / "scripts/ci"


def load_generic(work):
	scripts = fetch_publisher(work)
	sys.path.insert(0, str(scripts))
	import package_chart as generic  # oan-helm's, fetched above

	return generic


def chart_version(env):
	"""0.0.1-develop.<BUILD_NUMBER>, the only develop version publish_chart.py puts in repository/develop/."""
	if env["BRANCH_NAME"] not in ("develop", "staging"):
		raise ValueError("only develop and staging package the chart, not %s" % env["BRANCH_NAME"])
	return "0.0.1-%s.%s" % (env["BRANCH_NAME"], env["BUILD_NUMBER"])


def app_version(env=None):
	"""The Frappe/app version declared in Chart.yaml (appVersion), used as single source of truth."""
	chart_yaml = ROOT / "helm" / CHART / "Chart.yaml"
	text = chart_yaml.read_text(encoding="utf-8")
	match = re.search(r"^appVersion:\s*[\"']?([^\"'\s]+)", text, re.M)
	if not match:
		raise ValueError("appVersion not found in %s" % chart_yaml)
	return match.group(1)


def app_version_build(env, work):
	print(app_version(env))
	return 0


def version_build(env, work):
	print(chart_version(env))
	return 0


def package_build(env, work):
	generic = load_generic(work)
	version = chart_version(env)
	if env["IMAGE_TAG"] != version:
		raise ValueError("image tag %s is not the chart version %s" % (env["IMAGE_TAG"], version))
	chart = work / CHART
	shutil.copytree(str(ROOT / "helm" / CHART), str(chart))
	values = chart / "values.yaml"
	values.write_text(
		generic.set_image(
			values.read_text(encoding="utf-8"), "frappe.image", env["IMAGE_REPOSITORY"], env["IMAGE_TAG"]
		),
		encoding="utf-8",
	)
	metadata = chart / "Chart.yaml"
	metadata.write_text(
		generic.annotate(
			metadata.read_text(encoding="utf-8"),
			{
				"oan.org/source-repository": "Centre-for-Open-Societal-Systems/oan_grievance_service",
				"oan.org/source-commit": env["SOURCE_COMMIT"],
				"oan.org/source-subject": env.get("COMMIT_SUBJECT", "")[:200],
				"oan.org/release-name": "grievance",
				"oan.org/release-namespace": "grievance",
				"oan.org/values-overlay": "helm/environments/%s/oan-grievance-service.yaml" % deploy_env(env),
			},
		),
		encoding="utf-8",
	)
	shutil.rmtree(str(OUTPUT), ignore_errors=True)
	OUTPUT.mkdir()
	run(["helm", "package", str(chart), "--version", version, "--destination", str(OUTPUT)])
	print("packaged %s-%s.tgz" % (CHART, version))
	return 0


def gateway_package_build(env, work):
	"""apisix/k8s as a chart of its own: the routes, versioned like the app chart and annotated with this commit."""
	generic = load_generic(work)
	version = chart_version(env)
	chart = work / GATEWAY_CHART
	shutil.copytree(str(ROOT / "apisix" / "k8s"), str(chart))
	metadata = chart / "Chart.yaml"
	metadata.write_text(
		generic.annotate(
			metadata.read_text(encoding="utf-8"),
			{
				"oan.org/source-repository": "Centre-for-Open-Societal-Systems/oan_grievance_service",
				"oan.org/source-commit": env["SOURCE_COMMIT"],
				"oan.org/source-subject": env.get("COMMIT_SUBJECT", "")[:200],
				"oan.org/release-name": "grievance-gateway",
				"oan.org/release-namespace": "grievance",
				"oan.org/values-overlay": "helm/environments/%s/oan-grievance-gateway.yaml" % deploy_env(env),
			},
		),
		encoding="utf-8",
	)
	shutil.rmtree(str(GATEWAY_OUTPUT), ignore_errors=True)
	GATEWAY_OUTPUT.mkdir()
	run(["helm", "package", str(chart), "--version", version, "--destination", str(GATEWAY_OUTPUT)])
	print("packaged %s-%s.tgz" % (GATEWAY_CHART, version))
	return 0


def deploy_env(env):
	return {"develop": "dev", "staging": "staging"}[env["BRANCH_NAME"]]


def retag(promote, repository, source_tag, tag):
	"""Add `tag` to the ECR image that has `source_tag`. The manifest is the same, so the image cannot change."""
	found = promote.ECR.match(repository)
	if found is None:
		raise ValueError("%s is not an ECR repository" % repository)
	base = ["aws", "ecr", "--region", found.group("region")]
	name = ["--repository-name", found.group("name")]
	manifest = promote.run(
		[
			*base,
			"batch-get-image",
			*name,
			"--image-ids",
			"imageTag=" + source_tag,
			"--query",
			"images[0].imageManifest",
			"--output",
			"text",
		]
	).rstrip("\n")
	if not manifest or manifest == "None":
		raise ValueError("%s has no image tagged %s" % (repository, source_tag))
	result = subprocess.run(
		[*base, "put-image", *name, "--image-tag", tag, "--image-manifest", manifest],
		capture_output=True,
		text=True,
	)
	if result.returncode and "ImageAlreadyExistsException" not in result.stderr:
		raise ValueError("%s: could not add tag %s: %s" % (repository, tag, result.stderr.strip()[:300]))


def promote_build(env, work):
	"""staging: the develop chart built from this very source tree, its image also tagged with the staging version.

	Nothing is rebuilt: staging only runs what develop built. Needs the aws CLI with ECR access; DRY_RUN=1 tags nothing."""
	load_generic(work)  # puts oan-helm's scripts/ci on sys.path
	import package_chart as generic
	import promote_chart as promote  # oan-helm's, fetched above

	version = chart_version(env)
	if env["IMAGE_TAG"] != version:
		raise ValueError("image tag %s is not the chart version %s" % (env["IMAGE_TAG"], version))
	tree = run(["git", "rev-parse", "HEAD^{tree}"]).strip()
	source = promote.channel()
	index = urllib.request.urlopen(source + "index.yaml", timeout=60).read().decode("utf-8")

	def tree_of(commit):
		try:
			return run(["git", "rev-parse", commit + "^{tree}"]).strip()
		except subprocess.CalledProcessError:
			return None

	entry = promote.built_from_tree(promote.index_entries(index, CHART), tree, tree_of)
	archive = work / "develop.tgz"
	promote.download("%s%s-%s.tgz" % (source, CHART, entry["version"]), archive, entry["digest"])
	with tarfile.open(str(archive)) as bundle:
		bundle.extractall(str(work))
	chart = work / CHART
	print("%s:%s -> %s" % (env["IMAGE_REPOSITORY"], entry["version"], version))
	if env.get("DRY_RUN") != "1":
		retag(promote, env["IMAGE_REPOSITORY"], entry["version"], version)
	values = chart / "values.yaml"
	values.write_text(
		generic.set_image(
			values.read_text(encoding="utf-8"), "frappe.image", env["IMAGE_REPOSITORY"], version
		),
		encoding="utf-8",
	)
	metadata = chart / "Chart.yaml"
	text = re.sub(
		r"(?m)^\s*oan\.org/(source-commit|source-subject|release-name|release-namespace|values-overlay):.*\n",
		"",
		metadata.read_text(encoding="utf-8"),
	)
	metadata.write_text(
		generic.annotate(
			text,
			{
				"oan.org/promoted-from": entry["version"],
				"oan.org/source-commit": env["SOURCE_COMMIT"],
				"oan.org/source-subject": env.get("COMMIT_SUBJECT", "")[:200],
				"oan.org/release-name": "grievance",
				"oan.org/release-namespace": "grievance",
				"oan.org/values-overlay": "helm/environments/staging/oan-grievance-service.yaml",
			},
		),
		encoding="utf-8",
	)
	shutil.rmtree(str(OUTPUT), ignore_errors=True)
	OUTPUT.mkdir()
	run(["helm", "package", str(chart), "--version", version, "--destination", str(OUTPUT)])
	print("packaged %s-%s.tgz" % (CHART, version))
	return 0


def published_commit(env, name, chart=CHART):
	"""oan.org/source-commit of the archive already in repository/<channel>/ of oan-helm, or None."""
	request = urllib.request.Request(
		"https://api.github.com/repos/%s/contents/repository/%s/%s"
		% (OAN_HELM_SLUG, env["BRANCH_NAME"], name),
		headers={
			"Accept": "application/vnd.github.raw",
			"Authorization": "Bearer " + env.get("GH_TOKEN", ""),
		},
	)
	try:
		with urllib.request.urlopen(request) as response:
			data = response.read()
		with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
			text = archive.extractfile("%s/Chart.yaml" % chart).read().decode("utf-8")
	except (OSError, KeyError, tarfile.TarError):
		return None
	match = re.search(r"oan\.org/source-commit: *\"?([0-9a-f]{7,40})", text)
	return match.group(1) if match else None


def publish_build(env, work, output=OUTPUT, chart=CHART):
	if env.get("DRY_RUN") != "1" and not env.get("GH_TOKEN"):
		print("FAIL: no GitHub credential: the chart was not published.")
		return 3
	archives = sorted(output.glob("*.tgz"))
	if len(archives) != 1:
		raise ValueError("expected the one archive of this build in %s" % output)
	scripts = fetch_publisher(work)
	result = subprocess.run([sys.executable, str(scripts / "publish_chart.py"), str(archives[0])], env=env)
	if result.returncode == ALREADY_PUBLISHED:
		# Build numbers can repeat (job recreated), so a taken version is only fine if it holds this commit.
		if published_commit(env, archives[0].name, chart) == env["SOURCE_COMMIT"]:
			print("%s was published by an earlier run of this commit." % archives[0].name)
			return 0
		print(
			"FAIL: %s is already published for another commit; the chart was not published."
			% archives[0].name
		)
		return 1
	return result.returncode


def gateway_publish_build(env, work):
	return publish_build(env, work, GATEWAY_OUTPUT, GATEWAY_CHART)


def main(argv=None, env=None):
	argv = sys.argv[1:] if argv is None else argv
	env = dict(os.environ if env is None else env)
	actions = {
		"version": version_build,
		"app-version": app_version_build,
		"package": package_build,
		"promote": promote_build,
		"publish": publish_build,
		"gateway-package": gateway_package_build,
		"gateway-publish": gateway_publish_build,
	}
	if len(argv) != 1 or argv[0] not in actions:
		print("usage: package_chart.py version|app-version|package|publish|gateway-package|gateway-publish")
		return 1
	work = Path(tempfile.mkdtemp(prefix="grievance-chart-"))
	try:
		return actions[argv[0]](env, work)
	except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as error:
		print("FAIL: %s: %s" % (error.__class__.__name__, error))
		return 1
	finally:
		shutil.rmtree(str(work), ignore_errors=True)


if __name__ == "__main__":
	sys.exit(main())
