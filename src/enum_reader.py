def status(axis_status):
    axis_status = int(axis_status)
    #Defining Bit Masks From AeroTech API
    jogging = 1 << 8

    #Need To Add Leading Zeros To Pack Enum To 26 Bits
    binary = bin(axis_status)[2:] #Get Raw Binary Number 
    pad_bin = int(binary.zfill(26)) #Pad To Make 26 Bits

    is_moving = pad_bin & jogging != 0

    return is_moving