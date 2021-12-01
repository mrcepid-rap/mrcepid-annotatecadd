#!/usr/bin/env python
# mrcepid-filterbcf 0.0.1
# Generated by dx-app-wizard.
#
# Author: Eugene Gardner (eugene.gardner at mrc.epid.cam.ac.uk)
#
# DNAnexus Python Bindings (dxpy) documentation:
#   http://autodoc.dnanexus.com/bindings/python/current/

import dxpy
import subprocess


# This function runs a command on an instance, either with or without calling the docker instance we downloaded
# By default, commands are not run via Docker, but can be changed by setting is_docker = True
def run_cmd(cmd: str, is_docker: bool = False) -> None:

    if is_docker:
        # -v here mounts a local directory on an instance (in this case the home dir) to a directory internal to the
        # Docker instance named /test/. This allows us to run commands on files stored on the AWS instance within Docker
        # This looks slightly different from other versions of this command I have written as CADD needs several resource
        # files. That means we have multiple mounts here to enable CADD to find them.
        cmd = "docker run " \
              "-v /home/dnanexus/cadd_files/:/CADD-scripts/data/annotations/ " \
              "-v /home/dnanexus/vep_cadd_files/:/CADD-scripts/data/prescored/GRCh38_v1.6/incl_anno/ " \
              "-v /home/dnanexus:/test " \
              "egardner413/mrcepid-annotatecadd " + cmd

    # Standard python calling external commands protocol
    print(cmd)
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate()

    # If the command doesn't work, print the error stream and close the AWS instance out with 'dxpy.AppError'
    if proc.returncode != 0:
        print("The following cmd failed:")
        print(cmd)
        print("STDERROR follows\n")
        print(stderr.decode('utf-8'))
        raise dxpy.AppError("Failed to run properly...")


# This is a helper function to upload a local file and then remove it from the instance.
# This is different than other applets I have written since CADD takes up so much space.
# I don't want to have to use a massive instance costing lots of £s!
def generate_linked_dx_file(file: str) -> dxpy.DXFile:

    linked_file = dxpy.upload_local_file(file)
    cmd = "rm " + file
    run_cmd(cmd)
    return linked_file


# This is just to compartmentalise the collection of all the resources I need for this task and
# get them into the right place
def ingest_resources() -> None:

    # Here we are downloading & unpacking resource files that are required for the annotation engine, they are:
    # 1. CADD reference files – These are the resource files so InDel CADD scores can be calculated from scratch
    dxpy.download_folder('project-G2XK5zjJXk83yZ598Z7BpGPk',
                         'cadd_files/',
                         folder = "/project_resources/cadd_files/")
    cmd = "tar -zxf cadd_files/annotationsGRCh38_v1.6.tar.gz -C cadd_files/"
    run_cmd(cmd)
    cmd = "rm cadd_files/annotationsGRCh38_v1.6.tar.gz"

    # 2. CADD known reference files - pre-computed sites files so we don't have to recompute CADD for SNVs
    dxpy.download_folder('project-G2XK5zjJXk83yZ598Z7BpGPk',
                         'vep_cadd_files/',
                         folder = "/project_resources/vep_cadd_files/")


# This function takes a VCF and performs CADD annotation on all variants in it.
def parse_vcf(vcfprefix: str) -> None:

    # Generate a sites file that is in the correct format for CADD
    cmd = "bcftools query -f '%CHROM\\t%POS\\t%ID\\t%REF\\t%ALT\\n' -o /test/variants.vcf /test/" + vcfprefix + ".vcf.gz"
    run_cmd(cmd, True)

    # CADD doesn't like the 'chr' prefix..., so remove it!
    cmd = 'sed -i \'s_chr__\' variants.vcf'
    run_cmd(cmd)

    # Run CADD on this file:
    cmd = 'CADD-scripts/CADD.sh -g GRCh38 -o /test/variants.cadd.tsv.gz /test/variants.vcf'
    run_cmd(cmd, True)

    # Add chr back so BCFtools can understand for reannotation and then gbzip and tabix index
    cmd = 'zcat variants.cadd.tsv.gz | tail -n+3 | sed \'s_^_chr_\' > variants.cadd.chr.tsv'
    run_cmd(cmd)
    cmd = "bgzip /test/variants.cadd.chr.tsv"
    run_cmd(cmd, True)
    cmd = "tabix -p vcf /test/variants.cadd.chr.tsv.gz"
    run_cmd(cmd, True)

    # Now annotate the original VCF with CADD scores:
    header_writer = open('variants.header.txt', 'w')
    header_writer.writelines('##INFO=<ID=CADD,Number=1,Type=Float,Description="CADD Phred Score">' + "\n")
    header_writer.close()
    cmd = "bcftools annotate --threads 8 -a /test/variants.cadd.chr.tsv.gz -c CHROM,POS,REF,ALT,-,CADD " \
          "-h /test/variants.header.txt -Oz -o /test/" + vcfprefix + ".cadd.vcf.gz /test/" + vcfprefix + ".vcf.gz"
    run_cmd(cmd, True)
    cmd = "bcftools index --threads 8 -t /test/" + vcfprefix + ".cadd.vcf.gz"
    run_cmd(cmd, True)

    # Remove CADD annotation files from this run to save space
    cmd = "rm variants.cadd.chr.tsv.gz"
    run_cmd(cmd)
    cmd = "rm variants.cadd.chr.tsv.gz.tbi"
    run_cmd(cmd)

    # And generate a TSV of all information from both this applet AND filterbcf for easy parsing by other users if they
    # want it:
    # -f here just provides bcftools query with specific INFO fields that we want to print.
    cmd = 'bcftools query -f ' \
          '"%CHROM\\t%POS\\t%REF\\t%ALT\\t%ID\\t%FILTER\\t%INFO/AF\\t%F_MISSING\\t%AN\\t%AC\\t%MANE\\t%ENST\\t%ENSG\\t%BIOTYPE\\t' \
          '%SYMBOL\\t%CSQ\\t%gnomAD_AF\\t%CADD\\t%REVEL\\t%SIFT\\t%POLYPHEN\\t%LOFTEE\\t%PARSED_CSQ\\t%MULTI\\t%INDEL\\t%MINOR\\t' \
          '%MAJOR\\t%MAF\\t%MAC\\n" -o /test/' + vcfprefix + '.vep.tsv /test/' + vcfprefix + ".cadd.vcf.gz"
    run_cmd(cmd, True)

    # Generate a tsv of all alternate alleles at sites with AF < 0.001 for three variant classes so we can get an idea of
    # how many of these variants exist per-person
    cmd = 'bcftools query -i \'%INFO/AF<0.001 & %gnomAD_AF < 0.001 & (%PARSED_CSQ == "PTV" | %PARSED_CSQ == "SYN" | %PARSED_CSQ == "MISSENSE") && GT="alt"\' ' \
          '-f "[%SAMPLE\\t%PARSED_CSQ\\t%INFO/AF\\t%LOFTEE\\n]" -o /test/' + vcfprefix + '.per_indv.tsv /test/' + vcfprefix + ".cadd.vcf.gz"
    run_cmd(cmd, True)

    # And bgzip and tabix this file
    cmd = "bgzip /test/" + vcfprefix + ".vep.tsv"
    run_cmd(cmd, True)
    cmd = "tabix -p vcf /test/" + vcfprefix + ".vep.tsv.gz"
    run_cmd(cmd, True)


@dxpy.entry_point('main')
def main(input_vcfs):

    # Bring our docker image into our environment so that we can run commands we need:
    cmd = "docker pull egardner413/mrcepid-annotatecadd:latest"
    run_cmd(cmd)

    # Separate function to acquire necessary resource files
    ingest_resources()

    # Run through each VCF file provided and add CADD annotation.
    # input_vcfs is simple an array of DNANexus file hashes that I dereference below
    # Each of these arrays will hold output files generated by this loop
    output_vcfs = [] # main VCFs
    output_vcf_idxs = [] # indicies for these VCFs
    output_veps = [] # tabix-indexed TSV files containing all annotations for each variant
    output_vep_idxs = [] # indicies for these TSV files
    output_per_samples = [] # per sample variants
    # Loop through each VCF and do CADD annotation
    for vcf in input_vcfs:

        # Download the VEP annotated VCF from mrc-filterbcf to this instance and name appropriately
        vcf = dxpy.DXFile(vcf)
        vcfprefix = vcf.describe()['name'].split(".vcf.gz")[0] # Set a prefix name for all files:
        dxpy.download_dxfile(vcf.get_id(), vcfprefix + ".vcf.gz") # Actually download the file

        # Print all variants in simple format parsable by CADD:
        parse_vcf(vcfprefix)

        # Set output
        output_vcf = vcfprefix + ".cadd.vcf.gz"
        output_vcf_idx = vcfprefix + ".cadd.vcf.gz.tbi"
        output_vep = vcfprefix + ".vep.tsv.gz"
        output_vep_idx = vcfprefix + ".vep.tsv.gz.tbi"
        output_per_sample = vcfprefix + ".per_indv.tsv"

        # Here I am using a function I built for this tool (generated_linked_dx_file()) to conserve space.
        # This function does all the standard "upload to DNANexus" stuff, but also removes the original file
        # from the instance to save space.
        output_vcfs.append(generate_linked_dx_file(output_vcf))
        output_vcf_idxs.append(generate_linked_dx_file(output_vcf_idx))
        output_veps.append(generate_linked_dx_file(output_vep))
        output_vep_idxs.append(generate_linked_dx_file(output_vep_idx))
        output_per_samples.append(generate_linked_dx_file(output_per_sample))

    # Getting files back into your project directory on DNAnexus is a two-step process:
    # 1. uploading the local file to the DNA nexus platform to assign it a file-ID (looks like file-ABCDEFGHIJKLMN1234567890)
    #       * this is done above by generate_linked_dx_file()
    # 2. linking this file ID to your project and placing it within your project's directory structure
    #       * this is done below
    # (the subdirectory can be controlled on the command-line by adding a flag to `dx run` like: --destination test/)
    # This is a strange python structure to me (coming from Java land), but what is essentially happening here is a for loop
    # that just runs the function dxlink() on each item in the arrays that we created above holding specific output files.
    output = {"output_vcfs": [dxpy.dxlink(item) for item in output_vcfs],
              "output_vcf_idxs": [dxpy.dxlink(item) for item in output_vcf_idxs],
              "output_veps": [dxpy.dxlink(item) for item in output_veps],
              "output_vep_idxs": [dxpy.dxlink(item) for item in output_vep_idxs],
              "output_per_samples": [dxpy.dxlink(item) for item in output_per_samples]}
    return output

dxpy.run()
